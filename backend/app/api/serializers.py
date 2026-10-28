"""Serialization helpers shared by API routers."""
from __future__ import annotations

from ..core.states import LABELS
from ..core.text import loads
from ..models import Application, Document, Job, aware


def iso(dt):
    dt = aware(dt)
    return dt.isoformat() if dt else None


def job_summary(j: Job) -> dict:
    bd = loads(j.score_breakdown_json, {}) or {}
    lpa = bd.get("annual_lpa") or [None, None]
    return {
        "id": j.id, "title": j.title, "company": j.company, "location": j.location, "work_mode": j.work_mode,
        "is_remote": j.is_remote, "provider": j.provider, "score": j.score, "deterministic_score": j.deterministic_score,
        "ai_score": j.ai_score, "tier": j.tier, "hard_excluded": j.hard_excluded,
        "exclusion_reasons": loads(j.exclusion_reasons_json, []), "flags": bd.get("flags", []),
        "salary": {"min": j.salary_min, "max": j.salary_max, "currency": j.salary_currency, "period": j.salary_period,
                   "kind": j.salary_kind, "source": j.salary_source, "text": j.salary_text,
                   "annual_lpa_min": lpa[0], "annual_lpa_max": lpa[1] if len(lpa) > 1 else None},
        "posted_at": iso(j.posted_at), "posted_at_evidence": j.posted_at_evidence, "first_seen_at": iso(j.first_seen_at),
        "last_verified_at": iso(j.last_verified_at), "listing_status": j.listing_status,
        "pipeline_status": j.pipeline_status, "pipeline_label": LABELS.get(j.pipeline_status, j.pipeline_status),
        "apply_url": j.apply_url, "source_url": j.source_url, "duplicate_of_id": j.duplicate_of_id,
        "min_years_required": j.min_years_required,
    }


def job_detail(j: Job) -> dict:
    d = job_summary(j)
    d.update({
        "description": j.description, "department": j.department, "employment_type": j.employment_type,
        "experience_text": j.experience_text, "locations": loads(j.locations_json, []), "board": j.board,
        "source_job_id": j.source_job_id, "required_skills": loads(j.required_skills_json, []),
        "preferred_skills": loads(j.preferred_skills_json, []), "breakdown": loads(j.score_breakdown_json, {}),
        "ai_analysis": loads(j.ai_analysis_json, None), "closed_reason": j.closed_reason, "content_hash": j.content_hash,
        "scored_at": iso(j.scored_at), "country": j.country,
    })
    return d


def document_dict(doc: Document, include_content: bool = False) -> dict:
    d = {
        "id": doc.id, "job_id": doc.job_id, "kind": doc.kind, "generation_id": doc.generation_id,
        "generator": doc.generator, "model": doc.model, "prompt_version": doc.prompt_version,
        "source_resume_id": doc.source_resume_id, "source_resume_sha256": doc.source_resume_sha256,
        "profile_hash": doc.profile_hash, "job_content_hash": doc.job_content_hash, "created_at": iso(doc.created_at),
        "validation": loads(doc.validation_json, {}), "has_pdf": bool(doc.pdf_path), "has_docx": bool(doc.docx_path),
        "has_tex": bool(doc.tex_source), "tex_origin": doc.tex_origin, "page_count": doc.page_count,
    }
    if include_content:
        d["content"] = loads(doc.content_json, {})
    return d


def application_dict(app: Application) -> dict:
    return {
        "id": app.id, "job_id": app.job_id, "resume_document_id": app.resume_document_id,
        "cover_document_id": app.cover_document_id, "answers": loads(app.answers_json, []),
        "checklist": loads(app.checklist_json, {}), "concerns": loads(app.concerns_json, []),
        "approved_at": iso(app.approved_at), "submitted_at": iso(app.submitted_at),
        "submission_method": app.submission_method, "submission_evidence": app.submission_evidence,
        "deadline": iso(app.deadline), "notes": app.notes, "history": loads(app.history_json, []),
        "updated_at": iso(app.updated_at),
    }
