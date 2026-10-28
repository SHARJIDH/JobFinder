"""Daily workflow orchestration: discover → dedupe → verify → rank → tailor → notify."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.text import dumps, loads
from ..core.yaml_config import Profile, SearchConfig, load_profile, load_search_config
from ..llm.service import BudgetExceeded, LLMError, LLMService
from ..llm.tasks import PROMPT_VERSION_MATCH, AIMatch, candidate_facts, clamp_match, match_prompt
from ..models import Document, Job, Run, aware, utcnow
from ..sources.http import PoliteClient
from ..sources.providers import FetchContext, fetch_source, verify_posting
from .activity import log_event
from .applications import advance_to
from .discovery import upsert_result
from .notify import send_daily_digest
from .profile_facts import evidence, usable_skills
from .ranking import TIER_BASE, score_input_hash, score_job, tier_for
from .tailoring import generate_documents


def fetch_context(cfg: SearchConfig) -> FetchContext:
    kws = [k for r in cfg.roles for k in r.keywords]
    return FetchContext(role_keywords=kws, exclude_keywords=cfg.exclude_title_keywords,
                        home_country=cfg.locations.home_country,
                        workday_max_details=cfg.limits.workday_max_details_per_company)


def discover(db: Session, cfg: SearchConfig, http: PoliteClient, run_id: int | None = None,
             only_company: str | None = None, llm: LLMService | None = None, web_search: bool = True) -> dict:
    ctx = fetch_context(cfg)
    stats = {"sources": 0, "fetched": 0, "new": 0, "updated": 0, "duplicates": 0, "ineligible": 0, "too_old": 0,
             "closed": 0, "errors": [], "new_job_ids": [], "web_leads": 0, "web_verified": 0}

    def ingest(company_name: str, label: str, res) -> None:
        stats["fetched"] += res.fetched
        if res.error:
            stats["errors"].append(f"{company_name}/{label}: {res.error}")
            log_event(db, "discovery", f"{company_name} ({label}): {res.error}", level="error", run_id=run_id)
        up = upsert_result(db, res, cfg)
        for k in ("new", "updated", "duplicates", "ineligible", "too_old", "closed"):
            stats[k] += getattr(up, k)
        stats["new_job_ids"] += up.new_job_ids
        log_event(db, "discovery", f"{company_name} ({label}): fetched {res.fetched}, relevant {len(res.postings)}, "
                                   f"new {up.new}, dup {up.duplicates}, closed {up.closed}", run_id=run_id,
                  data={"skipped_title": res.skipped, "ineligible": up.ineligible, "too_old": up.too_old})
        db.commit()

    for company in cfg.companies:
        if only_company and company.name.lower() != only_company.lower():
            continue
        for src in company.sources:
            if not src.enabled:
                continue
            stats["sources"] += 1
            ctx.known_ids = set(db.scalars(select(Job.source_job_id).where(Job.provider == src.provider, Job.board == src.board)))
            res = fetch_source(http, src.provider, src.board, company.name, ctx, src.search_terms)
            ingest(company.name, src.provider, res)

    if web_search and cfg.web_search.enabled and llm is not None and llm.available:
        from .websearch import SearchBudget, companies_to_search, search_company

        budget = SearchBudget(cfg.web_search.max_queries_per_run, cfg.web_search.max_page_fetches_per_run)
        ctx.known_ids = set()
        for company in companies_to_search(cfg):
            if only_company and company.name.lower() != only_company.lower():
                continue
            if budget.queries_left <= 0:
                break
            try:
                res = search_company(http, llm, company, cfg, ctx, budget)
            except BudgetExceeded as e:
                stats["errors"].append(f"web search stopped: {e}")
                log_event(db, "llm", f"Web search stopped: {e}", level="warn", run_id=run_id)
                break
            except LLMError as e:
                stats["errors"].append(f"{company.name}/websearch: {e}")
                log_event(db, "discovery", f"{company.name} (web search): {e}", level="error", run_id=run_id)
                continue
            stats["web_leads"] += sum(1 for p in res.postings if p.is_lead)
            stats["web_verified"] += sum(1 for p in res.postings if not p.is_lead)
            ingest(company.name, "web search", res)
        for line in budget.log[:50]:
            log_event(db, "discovery", line, level="info", run_id=run_id)
        db.commit()
    return stats


def reverify(db: Session, http: PoliteClient, limit: int = 30, run_id: int | None = None) -> dict:
    """Re-check jobs in the active workflow whose source cannot prove closure by absence."""
    cutoff = utcnow() - timedelta(hours=20)
    jobs = db.scalars(select(Job).where(Job.listing_status == "open", Job.duplicate_of_id.is_(None),
                                        Job.pipeline_status.in_(["shortlisted", "resume_ready", "ready_for_review", "approved"]))
                      .order_by(Job.last_verified_at)).all()
    checked = closed = 0
    for job in jobs:
        if checked >= limit:
            break
        lv = aware(job.last_verified_at)
        if lv and lv > cutoff:
            continue
        ok = verify_posting(http, job.provider, job.board, job.source_job_id, job.source_url)
        checked += 1
        if ok is True:
            job.last_verified_at = utcnow()
        elif ok is False:
            job.listing_status, job.closed_reason, job.last_verified_at = "closed", "Source reports posting removed", utcnow()
            closed += 1
            log_event(db, "verify", f"Closed: {job.company} — {job.title}", job_id=job.id, run_id=run_id)
    db.commit()
    return {"checked": checked, "closed": closed}


def rank_jobs(db: Session, cfg: SearchConfig, profile: Profile, llm: LLMService | None, *, force: bool = False,
              job_ids: list[int] | None = None, use_ai: bool = True, run_id: int | None = None) -> dict:
    ev = evidence(db, profile)
    resume_text, resume_sha = ev.resume_text, ev.fingerprint
    usable = usable_skills(profile, resume_text)
    q = select(Job).where(Job.duplicate_of_id.is_(None))
    if job_ids:
        q = q.where(Job.id.in_(job_ids))
    else:
        q = q.where(Job.listing_status == "open")
    scored = skipped = 0
    candidates: list[Job] = []
    for job in db.scalars(q):
        h = score_input_hash(job, profile, cfg, resume_sha)
        if not force and job.score_input_hash == h:
            skipped += 1
            if (job.ai_score is None and not job.hard_excluded
                    and (job.deterministic_score or 0) >= cfg.limits.ai_score_min_deterministic):
                candidates.append(job)
            continue
        ai = loads(job.ai_analysis_json, {}) or {}
        ai_score = ai.get("fit_score") if ai.get("content_hash") == job.content_hash and ai.get("profile_hash") == profile.fingerprint() else None
        r = score_job(job, profile, cfg, usable, ai_score=ai_score)
        _apply_score(job, r, h)
        scored += 1
        if ai_score is None and not r.hard_excluded and r.deterministic >= cfg.limits.ai_score_min_deterministic:
            candidates.append(job)
    db.commit()

    ai_done, ai_errors = 0, []
    if use_ai and llm and llm.available and candidates:
        facts = candidate_facts(profile, usable)
        candidates.sort(key=lambda j: -(j.deterministic_score or 0))
        for job in candidates[: cfg.limits.max_ai_scores_per_run]:
            try:
                system, prompt = match_prompt(job, facts)
                m, meta = llm.structured(task="match", system=system, prompt=prompt, schema=AIMatch, tier="fast",
                                         prompt_version=PROMPT_VERSION_MATCH, max_output_tokens=900)
            except BudgetExceeded as e:
                ai_errors.append(str(e))
                log_event(db, "llm", str(e), level="warn", run_id=run_id)
                break
            except LLMError as e:
                ai_errors.append(str(e)[:200])
                log_event(db, "llm", f"AI match failed for job {job.id}: {e}", level="error", run_id=run_id, job_id=job.id)
                continue
            m = clamp_match(m)
            job.ai_analysis_json = dumps(m.model_dump() | {"model": meta["model"], "content_hash": job.content_hash,
                                                           "profile_hash": profile.fingerprint(), "at": utcnow().isoformat()})
            r = score_job(job, profile, cfg, usable, ai_score=float(m.fit_score))
            _apply_score(job, r, job.score_input_hash)
            ai_done += 1
        db.commit()
    return {"scored": scored, "unchanged": skipped, "ai_scored": ai_done, "ai_errors": ai_errors}


def _apply_score(job: Job, r, h: str) -> None:
    job.deterministic_score, job.ai_score, job.score = r.deterministic, r.ai_score, r.final
    job.tier, job.rank_value, job.hard_excluded = r.tier, r.rank_value, r.hard_excluded
    job.exclusion_reasons_json = dumps(r.exclusion_reasons)
    job.score_breakdown_json = dumps(r.breakdown())
    job.score_input_hash, job.scored_at = h, utcnow()


def retier_all(db: Session, cfg: SearchConfig) -> None:
    for job in db.scalars(select(Job).where(Job.score.is_not(None))):
        job.tier = tier_for(job.score, cfg, job.hard_excluded)
        job.rank_value = TIER_BASE[job.tier] + (job.rank_value or 0) % 1000


def auto_tailor(db: Session, cfg: SearchConfig, profile: Profile, llm: LLMService | None, run_id: int | None = None) -> dict:
    tiers = ["A"] if cfg.limits.auto_tailor_min_tier == "A" else ["A", "B"]
    jobs = db.scalars(select(Job).where(Job.tier.in_(tiers), Job.listing_status == "open", Job.duplicate_of_id.is_(None),
                                        Job.pipeline_status.in_(["discovered", "shortlisted"]))
                      .order_by(Job.rank_value.desc())).all()
    done = 0
    for job in jobs:
        if done >= cfg.limits.auto_tailor_per_run:
            break
        if db.scalars(select(Document).where(Document.job_id == job.id)).first():
            continue
        generate_documents(db, job, profile, llm)
        advance_to(db, job, "resume_ready", "auto-tailored by daily run")
        done += 1
        db.commit()
    return {"tailored": done}


def run_daily(db: Session, *, http: PoliteClient | None = None, llm: LLMService | None = None,
              notify: bool = True) -> Run:
    cfg, profile = load_search_config(), load_profile()
    run = Run(kind="daily")
    db.add(run)
    db.commit()
    http = http or PoliteClient()
    llm = llm if llm is not None else LLMService(db)
    stats: dict = {}
    try:
        stats["discovery"] = discover(db, cfg, http, run.id, llm=llm)
        stats["verification"] = reverify(db, http, run_id=run.id)
        stats["ranking"] = rank_jobs(db, cfg, profile, llm, run_id=run.id)
        stats["tailoring"] = auto_tailor(db, cfg, profile, llm, run.id)
        if notify:
            d = send_daily_digest(db, cfg)
            stats["notification"] = {"jobs": len(d["jobs"]), "sent": d["sent"]}
        stats["llm"] = llm.budget_status()
        run.status = "partial" if stats["discovery"]["errors"] else "success"
    except Exception as e:
        db.rollback()
        run.status = "failed"
        stats["fatal"] = f"{type(e).__name__}: {e}"[:1000]
        log_event(db, "pipeline", f"Daily run failed: {stats['fatal']}", level="error", run_id=run.id)
        raise
    finally:
        stats.get("discovery", {}).pop("new_job_ids", None)
        run.stats_json = dumps(stats)
        run.finished_at = utcnow()
        db.add(run)
        db.commit()
    return run
