"""Normalization, location eligibility, deduplication and closure detection."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.parsing import parse_location, parse_min_years
from ..core.skills import extract_required_preferred
from ..core.states import PRE_SUBMISSION
from ..core.text import canonical_url, dumps, normalize_location, normalize_title, sha256_text
from ..core.yaml_config import SearchConfig
from ..models import Job, aware, utcnow
from ..sources.base import RawPosting, SourceResult


def fingerprint(company: str, title: str, location: str) -> str:
    return sha256_text(company.lower().strip(), normalize_title(title), normalize_location(location))


def content_hash(p: RawPosting) -> str:
    return sha256_text(p.title, p.location, p.description)


@dataclass
class Eligibility:
    eligible: bool
    reason: str = ""
    notes: list[str] = field(default_factory=list)


def location_eligibility(p: RawPosting, cfg: SearchConfig) -> Eligibility:
    """Hard location constraint check. Only explicit mismatches are excluded."""
    prefs = cfg.locations
    all_locs = [p.location, *p.locations, p.country_hint]
    info = parse_location(*all_locs, workplace_hint=p.workplace_hint)
    home = prefs.home_country.lower()
    in_home = info.in_india if home == "india" else any(home in (x or "").lower() for x in all_locs)
    if in_home:
        if info.cities and not prefs.allow_other_home_country_cities:
            if not set(info.cities) & set(prefs.preferred_cities) and not info.is_remote:
                return Eligibility(False, "location: outside preferred cities")
        return Eligibility(True)
    loc_text = " ".join(x for x in all_locs if x).lower()
    if any(c.lower() in loc_text for c in prefs.international_eligible_countries):
        return Eligibility(True, notes=["international location you marked as eligible"])
    if info.is_remote and prefs.allow_remote:
        if any(re.search(r"\b" + re.escape(r.lower()) + r"\b", loc_text) for r in prefs.remote_regions_eligible):
            return Eligibility(True, notes=["remote role; confirm candidate-location eligibility"])
        # A remote role that names a specific place is restricted to that place.
        remainder = re.sub(r"\b(remote|hybrid|work from home|wfh|distributed|flexible|first|friendly|or|and)\b", " ", loc_text)
        remainder = re.sub(r"[^a-z]+", " ", remainder).strip()
        if remainder:
            return Eligibility(False, f"location: remote restricted to {remainder[:60]}")
        return Eligibility(True, notes=["remote role with unspecified region; verify eligibility"])
    if not loc_text.strip():
        return Eligibility(True, notes=["location not stated"])
    return Eligibility(False, "location: outside eligible countries")


def normalize_posting(p: RawPosting) -> dict:
    info = parse_location(p.location, *p.locations, p.country_hint, workplace_hint=p.workplace_hint)
    req, pref = extract_required_preferred(p.description)
    min_years, exp_text = parse_min_years(p.description)
    s = p.salary
    return dict(
        provider=p.provider, board=p.board, source_job_id=p.source_job_id, source_url=p.source_url,
        apply_url=p.apply_url or p.source_url, canonical_url=canonical_url(p.source_url),
        fingerprint=fingerprint(p.company, p.title, p.location),
        company=p.company, title=p.title[:300], department=p.department[:250], location=p.location[:500],
        locations_json=dumps(p.locations), country="India" if info.in_india else p.country_hint[:80],
        is_remote=info.is_remote, work_mode=info.work_mode, employment_type=p.employment_type[:60],
        experience_text=exp_text[:500], min_years_required=min_years, description=p.description,
        content_hash=content_hash(p), required_skills_json=dumps(req), preferred_skills_json=dumps(pref),
        salary_min=s.min, salary_max=s.max, salary_currency=s.currency, salary_period=s.period,
        salary_kind=s.kind if s.min is not None or s.max is not None else "unknown",
        salary_source=s.source, salary_text=s.text[:500],
        posted_at=p.posted_at, posted_at_evidence=p.posted_at_evidence[:200],
    )


@dataclass
class UpsertStats:
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates: int = 0
    ineligible: int = 0
    too_old: int = 0
    closed: int = 0
    new_job_ids: list[int] = field(default_factory=list)


def _find_duplicate(db: Session, fields: dict, is_lead: bool = False) -> Job | None:
    """Canonical URL match, or same company/title/location from a *different* source.
    Within one ATS board, distinct ids are distinct requisitions unless the content is identical (a repost)."""
    q = select(Job).where(Job.duplicate_of_id.is_(None),
                          Job.listing_status.in_(["open", "unverified"] if is_lead else ["open"]))
    if fields["canonical_url"]:
        hit = db.scalars(q.where(Job.canonical_url == fields["canonical_url"])).first()
        if hit:
            return hit
    for cand in db.scalars(q.where(Job.fingerprint == fields["fingerprint"])):
        if is_lead and cand.listing_status == "unverified":
            continue  # two leads with the same title/city may be different requisitions; only URLs prove identity
        same_board = cand.provider == fields["provider"] and cand.board == fields["board"]
        if not same_board or cand.content_hash == fields["content_hash"]:
            return cand
    return None


def _absorb_leads(db: Session, job: Job) -> None:
    """A verified posting supersedes any earlier web-search lead for the same job."""
    q = select(Job).where(Job.listing_status == "unverified", Job.duplicate_of_id.is_(None), Job.id != job.id,
                          (Job.fingerprint == job.fingerprint) | (Job.canonical_url == job.canonical_url))
    for lead in db.scalars(q):
        if lead.pipeline_status == "discovered":
            lead.duplicate_of_id = job.id


def upsert_result(db: Session, result: SourceResult, cfg: SearchConfig, now: datetime | None = None,
                  skip_eligibility: bool = False) -> UpsertStats:
    now = now or utcnow()
    stats = UpsertStats()
    max_age = timedelta(days=cfg.freshness.max_age_days)
    for p in result.postings:
        elig = Eligibility(True) if skip_eligibility else location_eligibility(p, cfg)
        existing = db.scalars(select(Job).where(Job.provider == p.provider, Job.source_job_id == p.source_job_id)).first()
        if not elig.eligible and existing is None:
            stats.ineligible += 1
            continue
        if existing is None and p.posted_at and now - p.posted_at > max_age and not skip_eligibility:
            stats.too_old += 1
            continue
        fields = normalize_posting(p)
        if existing:
            existing.last_seen_at = now
            if p.is_lead:
                continue  # a lead never overwrites a stored posting
            existing.last_verified_at = now
            if existing.listing_status in ("closed", "unverified"):
                existing.listing_status, existing.closed_reason = "open", ""
                if existing.pipeline_status == "closed":
                    existing.pipeline_status = "discovered"
            if existing.content_hash != fields["content_hash"]:
                for k, v in fields.items():
                    if k not in ("fingerprint",):
                        setattr(existing, k, v)
                existing.score_input_hash = ""  # force re-score
                stats.updated += 1
            else:
                stats.unchanged += 1
            continue
        job = Job(**fields, first_seen_at=now, last_seen_at=now, last_verified_at=None if p.is_lead else now,
                  listing_status="unverified" if p.is_lead else "open")
        dup = _find_duplicate(db, fields, p.is_lead)
        if dup is not None:
            job.duplicate_of_id = dup.id
            stats.duplicates += 1
        db.add(job)
        db.flush()
        if dup is None:
            stats.new += 1
            stats.new_job_ids.append(job.id)
            if not p.is_lead:
                _absorb_leads(db, job)
    if result.complete and not result.error:
        stats.closed = mark_missing_closed(db, result, now)
    return stats


def mark_missing_closed(db: Session, result: SourceResult, now: datetime) -> int:
    """A job that was on a board but is absent from a *complete* fresh listing is closed."""
    open_jobs = db.scalars(select(Job).where(Job.provider == result.provider, Job.board == result.board,
                                             Job.listing_status == "open")).all()
    n = 0
    for job in open_jobs:
        if job.source_job_id in result.seen_ids:
            continue
        job.listing_status = "closed"
        job.closed_reason = f"No longer listed on {result.provider} board '{result.board}'"
        job.last_verified_at = now
        if job.pipeline_status in PRE_SUBMISSION:
            job.pipeline_status = "closed"
        n += 1
    return n


def manual_posting(company: str, title: str, url: str, location: str, description: str,
                   posted_at: datetime | None = None) -> RawPosting:
    """User-supplied posting (e.g. from a career site without a public API)."""
    from ..core.parsing import parse_salary_text

    return RawPosting(
        provider="manual", board=company.lower(), source_job_id=sha256_text(canonical_url(url) or title + company)[:24],
        company=company, title=title, source_url=url, apply_url=url, location=location, description=description,
        posted_at=posted_at, posted_at_evidence="entered by user" if posted_at else "",
        salary=parse_salary_text(description),
    )


def is_fresh(job: Job, hours: int) -> bool:
    ref = aware(job.posted_at)
    return bool(ref and datetime.now(timezone.utc) - ref <= timedelta(hours=hours))
