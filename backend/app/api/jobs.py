from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import get_settings
from ..core.states import STATES, TransitionError
from ..core.text import dumps, loads
from ..core.yaml_config import load_profile, load_search_config
from ..db import get_db
from ..llm.service import LLMService
from ..models import Application, Document, Job, utcnow
from ..services.activity import log_event
from ..services.applications import advance_to, prepare_application, set_status
from ..services.discovery import manual_posting, upsert_result
from ..services.pipeline import rank_jobs
from ..services.tailoring import generate_documents
from ..sources.base import SourceResult
from ..sources.http import PoliteClient, SourceBlocked
from ..sources.jsonld import fetch_jsonld_posting
from ..sources.providers import verify_posting
from .serializers import application_dict, document_dict, job_detail, job_summary

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


def _job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    q: str = "", company: str = "", tier: str = "", status: str = "", listing: str = "open",
    min_score: float | None = None, min_lpa: float | None = None, salary: str = "",
    fresh_hours: int | None = None, location: str = "", include_excluded: bool = False,
    include_duplicates: bool = False, sort: str = "rank", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(Job)
    if not include_duplicates:
        stmt = stmt.where(Job.duplicate_of_id.is_(None))
    if listing in ("open", "closed", "unverified"):
        stmt = stmt.where(Job.listing_status == listing)
    if not include_excluded:
        stmt = stmt.where(Job.hard_excluded.is_(False))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Job.title).like(like), func.lower(Job.company).like(like),
                              func.lower(Job.description).like(like)))
    if company:
        stmt = stmt.where(Job.company.in_([c for c in company.split(",") if c]))
    if tier:
        stmt = stmt.where(Job.tier.in_(tier.upper().split(",")))
    if status:
        stmt = stmt.where(Job.pipeline_status.in_(status.split(",")))
    if min_score is not None:
        stmt = stmt.where(Job.score >= min_score)
    if location:
        stmt = stmt.where(or_(func.lower(Job.location).like(f"%{location.lower()}%"),
                              func.lower(Job.locations_json).like(f"%{location.lower()}%")))
    if salary == "known":
        stmt = stmt.where(Job.salary_kind != "unknown")
    elif salary == "unknown":
        stmt = stmt.where(Job.salary_kind == "unknown")
    if fresh_hours:
        cutoff = utcnow() - timedelta(hours=fresh_hours)
        stmt = stmt.where(or_(Job.posted_at >= cutoff, (Job.posted_at.is_(None)) & (Job.first_seen_at >= cutoff)))
    order = {
        "rank": [Job.rank_value.desc().nulls_last()], "score": [Job.score.desc().nulls_last()],
        "posted": [Job.posted_at.desc().nulls_last()], "first_seen": [Job.first_seen_at.desc()],
        "company": [Job.company, Job.title],
    }.get(sort, [Job.rank_value.desc().nulls_last()])
    rows = list(db.scalars(stmt.order_by(*order, Job.id.desc())))
    if min_lpa is not None:  # computed in Python from the stored breakdown (approximate for non-INR)
        def lpa(j: Job):
            v = (loads(j.score_breakdown_json, {}) or {}).get("annual_lpa") or [None, None]
            return v[1] if v[1] is not None else v[0]
        rows = [j for j in rows if (lpa(j) or 0) >= min_lpa]
    total = len(rows)
    items = rows[(page - 1) * page_size: page * page_size]
    companies = sorted({c for (c,) in db.execute(select(Job.company).distinct())})
    return {"total": total, "page": page, "page_size": page_size, "items": [job_summary(j) for j in items],
            "facets": {"companies": companies, "statuses": STATES}}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    d = job_detail(job)
    d["duplicates"] = [job_summary(x) for x in db.scalars(select(Job).where(Job.duplicate_of_id == job.id))]
    d["documents"] = [document_dict(x) for x in db.scalars(select(Document).where(Document.job_id == job.id).order_by(Document.id.desc()))]
    app = db.scalars(select(Application).where(Application.job_id == job.id)).first()
    d["application"] = application_dict(app) if app else None
    return d


class ManualJob(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=8, max_length=2000)
    location: str = Field("", max_length=300)
    description: str = Field(min_length=50, max_length=60000)
    posted_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("Use the official https:// posting URL")
        return v


@router.post("/jobs/manual")
def add_manual_job(body: ManualJob, db: Session = Depends(get_db)):
    cfg, profile = load_search_config(), load_profile()
    p = manual_posting(body.company, body.title, body.url, body.location, body.description, body.posted_at)
    res = SourceResult("manual", p.board, p.company, postings=[p], complete=False)
    # Manual jobs bypass the location pre-filter (the user chose to add it); ranking still flags issues.
    existing = db.scalars(select(Job).where(Job.provider == "manual", Job.source_job_id == p.source_job_id)).first()
    if existing:
        raise HTTPException(409, f"Already added (job {existing.id})")
    stats = upsert_result(db, res, cfg, skip_eligibility=True)
    db.commit()
    job = db.scalars(select(Job).where(Job.provider == "manual", Job.source_job_id == p.source_job_id)).first()
    if not job:
        raise HTTPException(400, "Could not store job")
    rank_jobs(db, cfg, profile, LLMService(db), job_ids=[job.id], force=True)
    log_event(db, "jobs", f"Manual job added: {job.company} — {job.title}", job_id=job.id, commit=True)
    return {"id": job.id, "duplicate_of_id": job.duplicate_of_id, "stats": stats.__dict__}


class StatusBody(BaseModel):
    status: str
    note: str = ""


class FetchUrlBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


@router.post("/jobs/fetch-url")
def fetch_url(body: FetchUrlBody):
    """Prefill the manual-add form from an official page's schema.org JobPosting (robots.txt respected)."""
    if not body.url.startswith("https://"):
        raise HTTPException(422, "Use the official https:// posting URL")
    http = PoliteClient()
    try:
        p = fetch_jsonld_posting(http, body.url, "", "manual", "")
    except SourceBlocked as e:
        raise HTTPException(422, f"This site does not allow automated reading ({e}). Paste the description manually.") from e
    except httpx.HTTPError as e:
        raise HTTPException(422, f"Could not read the page: {type(e).__name__}") from e
    finally:
        http.close()
    if p is None:
        raise HTTPException(422, "No structured job data found on that page (or the posting has expired). Paste the description manually.")
    return {"title": p.title, "location": ", ".join([p.location, *p.locations]).strip(", "), "description": p.description,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None, "url": p.source_url}


class ImportBody(BaseModel):
    description: str = Field(min_length=50, max_length=60000)
    location: str | None = Field(None, max_length=300)


@router.post("/jobs/{job_id}/import-description")
def import_description(job_id: int, body: ImportBody, db: Session = Depends(get_db)):
    """Turn an unverified web-search lead (or a manual job) into a full posting using text from the official page."""
    job = _job(db, job_id)
    if job.provider not in ("websearch", "manual"):
        raise HTTPException(400, "Descriptions can only be imported for web-search leads or manual jobs")
    from ..services.discovery import normalize_posting
    from ..sources.base import RawPosting

    p = RawPosting(provider=job.provider, board=job.board, source_job_id=job.source_job_id, company=job.company,
                   title=job.title, source_url=job.source_url, apply_url=job.apply_url,
                   location=body.location if body.location is not None else job.location, description=body.description,
                   posted_at=job.posted_at, posted_at_evidence=job.posted_at_evidence)
    from ..core.parsing import parse_salary_text
    p.salary = parse_salary_text(body.description)
    for k, v in normalize_posting(p).items():
        if k not in ("fingerprint", "canonical_url", "source_url", "apply_url"):
            setattr(job, k, v)
    job.listing_status, job.score_input_hash = "open", ""
    db.commit()
    rank_jobs(db, load_search_config(), load_profile(), LLMService(db), job_ids=[job.id], force=True)
    log_event(db, "jobs", f"Description imported: {job.company} — {job.title}", job_id=job.id, commit=True)
    db.refresh(job)
    return job_detail(job)


@router.post("/jobs/{job_id}/status")
def change_status(job_id: int, body: StatusBody, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    if body.status in ("approved", "submitted"):
        raise HTTPException(400, "Use the application approval / submission endpoints for this status")
    try:
        set_status(db, job, body.status, body.note or "changed by user")
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e
    db.commit()
    return job_summary(job)


@router.post("/jobs/{job_id}/verify")
def verify_job(job_id: int, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    if job.provider == "manual":
        return {"result": "unknown", "message": "Manual jobs cannot be verified automatically; open the official link."}
    http = PoliteClient()
    try:
        ok = verify_posting(http, job.provider, job.board, job.source_job_id, job.source_url)
    finally:
        http.close()
    if ok is True:
        job.last_verified_at, job.listing_status = utcnow(), "open"
    elif ok is False:
        job.listing_status, job.closed_reason, job.last_verified_at = "closed", "Source reports posting removed", utcnow()
    db.commit()
    return {"result": {True: "open", False: "closed", None: "unknown"}[ok], "listing_status": job.listing_status}


class RescoreBody(BaseModel):
    use_ai: bool = True


@router.post("/jobs/{job_id}/rescore")
def rescore(job_id: int, body: RescoreBody, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    if body.use_ai and job.ai_score is None:
        job.ai_analysis_json = ""
    cfg = load_search_config()
    if body.use_ai:
        cfg = cfg.model_copy(update={"limits": cfg.limits.model_copy(update={"ai_score_min_deterministic": 0})})
    stats = rank_jobs(db, cfg, load_profile(), LLMService(db), job_ids=[job.id], force=True, use_ai=body.use_ai)
    db.refresh(job)
    return {"stats": stats, "job": job_detail(job)}


class GenerateBody(BaseModel):
    use_ai: bool = True
    cover_letter: bool = True


@router.post("/jobs/{job_id}/generate")
def generate(job_id: int, body: GenerateBody, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    if job.pipeline_status not in ("discovered", "shortlisted", "resume_ready", "ready_for_review", "approved"):
        raise HTTPException(409, f"Cannot regenerate documents in status '{job.pipeline_status}'")
    profile = load_profile()
    resume_doc, cover_doc = generate_documents(db, job, profile, LLMService(db), want_cover=body.cover_letter, use_llm=body.use_ai)
    if job.pipeline_status in ("discovered", "shortlisted"):
        advance_to(db, job, "resume_ready", "documents generated")
    app = db.scalars(select(Application).where(Application.job_id == job.id)).first()
    if app:  # keep the workspace pointing at the newest documents; any approval is revoked
        from ..services.applications import revoke_approval
        app.resume_document_id = resume_doc.id
        app.cover_document_id = cover_doc.id if cover_doc else None
        revoke_approval(db, job, app, "documents regenerated")
    log_event(db, "documents", f"Generated documents ({resume_doc.generator}) for {job.company} — {job.title}", job_id=job.id)
    db.commit()
    return {"resume": document_dict(resume_doc, True), "cover_letter": document_dict(cover_doc, True) if cover_doc else None}


@router.post("/jobs/{job_id}/prepare")
def prepare(job_id: int, db: Session = Depends(get_db)):
    job = _job(db, job_id)
    profile = load_profile()
    llm = LLMService(db)
    resume_doc = db.scalars(select(Document).where(Document.job_id == job.id, Document.kind == "resume").order_by(Document.id.desc())).first()
    cover_doc = db.scalars(select(Document).where(Document.job_id == job.id, Document.kind == "cover_letter").order_by(Document.id.desc())).first()
    try:
        if resume_doc is None:
            resume_doc, cover_doc = generate_documents(db, job, profile, llm)
        app, warnings = prepare_application(db, job, profile, llm, resume_doc, cover_doc)
    except TransitionError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    db.commit()
    return {"application_id": app.id, "warnings": warnings}


# ------------------------------------------------------------------ documents

def _doc(db: Session, doc_id: int) -> Document:
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, db: Session = Depends(get_db)):
    return document_dict(_doc(db, doc_id), include_content=True)


@router.get("/documents/{doc_id}/download")
def download_document(doc_id: int, format: str = Query("pdf", pattern="^(pdf|docx|tex)$"), inline: bool = False,
                      db: Session = Depends(get_db)):
    d = _doc(db, doc_id)
    job = db.get(Job, d.job_id)
    profile = load_profile()
    safe = lambda s: "".join(ch for ch in s if ch.isalnum())[:30] or "x"  # noqa: E731
    name = f"{safe(profile.contact.full_name or 'Candidate')}_{safe(job.company if job else 'Company')}_{'Resume' if d.kind == 'resume' else 'CoverLetter'}.{format}"
    if format == "tex":
        if not d.tex_source:
            raise HTTPException(404, "No LaTeX source for this document")
        return Response(d.tex_source, media_type="application/x-tex",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    path = Path(d.pdf_path if format == "pdf" else d.docx_path).resolve()
    root = get_settings().documents_dir.resolve()
    if root not in path.parents or not path.exists():
        raise HTTPException(404, "File not available")
    media = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(path, media_type=media, filename=name,
                        content_disposition_type="inline" if inline and format == "pdf" else "attachment")


@router.get("/documents/{doc_id}/preview/{page}")
def preview_page(doc_id: int, page: int, db: Session = Depends(get_db)):
    """PNG render of one PDF page for the editor preview (works in every browser, no PDF plugin needed)."""
    from ..services.latex import page_images

    d = _doc(db, doc_id)
    path = Path(d.pdf_path or "").resolve()
    root = get_settings().documents_dir.resolve()
    if not d.pdf_path or root not in path.parents or not path.exists() or not 1 <= page <= 10:
        raise HTTPException(404, "Preview not available")
    images = page_images(path.read_bytes(), scale=1.6, max_pages=1, first=page - 1)
    if not images:
        raise HTTPException(404, "No such page")
    return Response(images[0], media_type="image/png", headers={"Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ LaTeX editor

def _latex_payload(d: Document) -> dict:
    from ..services.latex import compiler_path

    return {"id": d.id, "job_id": d.job_id, "source": d.tex_source, "origin": d.tex_origin, "page_count": d.page_count,
            "compile_log": d.compile_log, "compiler_available": bool(compiler_path()),
            "validation": loads(d.validation_json, {}), "updated": d.created_at.isoformat() if d.created_at else None}


def _latex_doc(db: Session, doc_id: int) -> Document:
    d = _doc(db, doc_id)
    if d.kind != "resume":
        raise HTTPException(400, "LaTeX editing is available for resumes only")
    return d


def _allowed_facts(db: Session, d: Document) -> tuple[str, list[str]]:
    from ..llm.tasks import candidate_facts
    from ..services.latex import tex_to_text
    from ..services.latex_tailor import template_resume
    from ..services.profile_facts import evidence, usable_skills

    profile = load_profile()
    usable = usable_skills(profile, evidence(db, profile).resume_text)
    job = db.get(Job, d.job_id)
    tpl = template_resume(db)
    allowed = "\n".join([candidate_facts(profile, usable, include_bullet_ids=False), d.content_json,
                         job.title if job else "", job.company if job else "",
                         tex_to_text(tpl.text_source()) if tpl else ""])
    return allowed, usable


def _store_latex(db: Session, d: Document, tex: str, origin: str, res, notes: list[str]) -> None:
    from ..services.applications import revoke_approval
    from ..services.latex import unsupported_changes

    d.tex_source, d.tex_origin, d.compile_log = tex, origin, res.log[:8000]
    if res.ok and res.pdf:
        Path(d.pdf_path).write_bytes(res.pdf)
        d.page_count = res.pages
    v = loads(d.validation_json, {}) or {}
    v["latex_notes"] = (v.get("latex_notes") or []) + notes
    if origin == "edited":
        allowed, usable = _allowed_facts(db, d)
        ch = unsupported_changes(tex, allowed, usable)
        v["latex_unverified"] = ch if (ch["numbers"] or ch["skills"]) else {}
    d.validation_json = dumps(v)
    for app in db.scalars(select(Application).where(Application.resume_document_id == d.id)):
        revoke_approval(db, db.get(Job, app.job_id), app, "LaTeX resume changed")


@router.get("/documents/{doc_id}/latex")
def get_latex(doc_id: int, db: Session = Depends(get_db)):
    return _latex_payload(_latex_doc(db, doc_id))


class LatexBody(BaseModel):
    source: str = Field(min_length=20, max_length=200000)


@router.put("/documents/{doc_id}/latex")
def save_latex(doc_id: int, body: LatexBody, db: Session = Depends(get_db)):
    """Save your edited LaTeX and recompile. Edits are yours, so they are not blocked — but any numbers or skills
    not found in your facts are flagged in the validation report."""
    from ..services.latex import compile_tex

    d = _latex_doc(db, doc_id)
    res = compile_tex(body.source)
    if not res.ok and res.log.startswith("Refused"):
        raise HTTPException(422, res.log)
    _store_latex(db, d, body.source, "edited", res, [])
    db.commit()
    out = _latex_payload(d)
    out["compile_ok"] = res.ok
    return out


@router.post("/documents/{doc_id}/latex/fit")
def fit_latex(doc_id: int, db: Session = Depends(get_db)):
    """Make the resume fit one page: tighter layout/trimming for the built-in template, otherwise Gemini looks at
    screenshots of the pages and shortens/re-spaces the LaTeX (never adding content)."""
    from ..services.latex import compile_tex
    from ..services.latex_tailor import LatexBuild, fit_builtin, fit_with_ai

    d = _latex_doc(db, doc_id)
    if not d.tex_source:
        raise HTTPException(400, "This resume has no LaTeX source; regenerate it")
    if d.tex_origin == "builtin":
        build = fit_builtin(loads(d.content_json, {}))
        if build.content is not None:
            c = loads(d.content_json, {})
            c.update({"experience": build.content["experience"], "projects": build.content["projects"]})
            d.content_json = dumps(c)
    else:
        llm = LLMService(db)
        if not llm.available:
            raise HTTPException(503, "One-page fitting of custom LaTeX needs Gemini (LLM_PROVIDER=gemini)")
        job = db.get(Job, d.job_id)
        allowed, usable = _allowed_facts(db, d)
        build = fit_with_ai(llm, LatexBuild(d.tex_source, d.tex_origin, compile_tex(d.tex_source)), usable, allowed, job)
    _store_latex(db, d, build.tex, d.tex_origin, build.result, build.notes)
    db.commit()
    return _latex_payload(d)


class AiEditBody(BaseModel):
    instruction: str = Field(min_length=3, max_length=1500)


@router.post("/documents/{doc_id}/latex/ai-edit")
def ai_edit_latex(doc_id: int, body: AiEditBody, db: Session = Depends(get_db)):
    """Ask Gemini to change the LaTeX (wording, order, spacing). Rejected if it adds facts you have not provided."""
    from ..llm.service import LLMError
    from ..services.latex import compile_tex
    from ..services.latex_tailor import LatexInvalid, revise_and_compile

    d = _latex_doc(db, doc_id)
    llm = LLMService(db)
    if not llm.available:
        raise HTTPException(503, "AI editing needs Gemini (LLM_PROVIDER=gemini)")
    allowed, usable = _allowed_facts(db, d)
    current = compile_tex(d.tex_source)
    try:
        tex, changes, res = revise_and_compile(llm, d.tex_source, body.instruction, usable, allowed, current.pdf, current.pages)
    except LatexInvalid as e:
        raise HTTPException(422, f"Gemini's edit was rejected: {e}") from e
    except LLMError as e:
        raise HTTPException(503, str(e)) from e
    if not res.ok:
        raise HTTPException(422, "Gemini's edit did not compile; nothing changed. " + res.log[:500])
    origin = d.tex_origin if d.tex_origin != "builtin" else "edited"
    _store_latex(db, d, tex, origin, res, [f"AI edit ({body.instruction[:80]}): " + "; ".join(changes)])
    db.commit()
    return _latex_payload(d)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    d = _doc(db, doc_id)
    for p in (d.pdf_path, d.docx_path):
        if p:
            Path(p).unlink(missing_ok=True)
    for app in db.scalars(select(Application).where(or_(Application.resume_document_id == d.id, Application.cover_document_id == d.id))):
        job = db.get(Job, app.job_id)
        from ..services.applications import revoke_approval
        revoke_approval(db, job, app, "document deleted")
    db.delete(d)
    db.commit()
    return {"deleted": doc_id}
