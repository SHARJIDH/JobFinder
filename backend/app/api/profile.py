from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import get_settings
from ..core.yaml_config import Profile, load_profile, parse_yaml_text, save_profile
from ..db import get_db
from ..llm.service import LLMError, LLMService
from ..llm.tasks import PROMPT_VERSION_PROFILE, ProfileDraft, profile_draft_prompt
from ..models import Application, Document, Job, MasterResume
from ..services.activity import log_event
from ..services.profile_builder import merge_draft
from ..services.profile_facts import included_resumes, resume_label, resume_skill_suggestions, skill_evidence
from ..services.resume_ingest import UploadError, store_resume
from .serializers import iso

router = APIRouter(prefix="/api/profile", dependencies=[Depends(require_auth)])


def _profile_payload(db: Session) -> dict:
    s = get_settings()
    profile = load_profile()
    resumes = list(db.scalars(select(MasterResume).order_by(MasterResume.id)))
    included = [r for r in resumes if r.is_active]
    text = "\n\n".join(r.text for r in included)
    return {
        "profile": profile.model_dump(),
        "yaml": s.profile_path.read_text(encoding="utf-8"),
        "years_of_experience": profile.years_of_experience(),
        "skill_evidence": [e.__dict__ for e in skill_evidence(profile, text, included)],
        "resume_suggestions": resume_skill_suggestions(profile, text),
        "resumes": [{"id": r.id, "label": resume_label(r), "filename": r.original_filename, "included": r.is_active,
                     "uploaded_at": iso(r.uploaded_at), "sha256": r.sha256, "size_bytes": r.size_bytes,
                     "text_preview": r.text[:4000], "is_latex": r.is_latex} for r in resumes],
        "context_notes": [n.model_dump() for n in profile.context_notes],
    }


@router.get("")
def get_profile(db: Session = Depends(get_db)):
    return _profile_payload(db)


class ProfileUpdate(BaseModel):
    yaml: str | None = Field(None, max_length=200000)
    profile: dict | None = None


@router.put("")
def update_profile(body: ProfileUpdate, db: Session = Depends(get_db)):
    try:
        if body.yaml is not None:
            profile = parse_yaml_text(body.yaml, Profile)
        elif body.profile is not None:
            profile = Profile.model_validate(body.profile)
        else:
            raise HTTPException(400, "Provide yaml or profile")
    except (yaml.YAMLError, ValidationError, ValueError) as e:
        raise HTTPException(422, f"Invalid profile: {str(e)[:800]}") from e
    save_profile(profile)
    log_event(db, "profile", "Profile updated", commit=True)
    return _profile_payload(db)


class Note(BaseModel):
    id: str = Field("", max_length=60)
    title: str = Field("", max_length=200)
    text: str = Field(min_length=1, max_length=20000)


class NotesBody(BaseModel):
    notes: list[Note] = Field(max_length=50)


@router.put("/notes")
def save_notes(body: NotesBody, db: Session = Depends(get_db)):
    """Free-text context in your own words. Used as candidate-provided facts for matching and tailoring."""
    profile = load_profile()
    data = profile.model_dump()
    data["context_notes"] = [n.model_dump() for n in body.notes if n.text.strip()]
    save_profile(Profile.model_validate(data))
    log_event(db, "profile", f"Context notes updated ({len(data['context_notes'])})", commit=True)
    return _profile_payload(db)


@router.post("/resume")
async def upload_resume(file: UploadFile = File(...), label: str = Form(""), db: Session = Depends(get_db)):
    s = get_settings()
    data = await file.read(s.max_upload_mb * 1024 * 1024 + 1)
    try:
        rec = store_resume(db, file.filename or "", data, label[:200])
    except UploadError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # malformed PDFs can raise many parser errors
        raise HTTPException(400, f"Could not read file: {type(e).__name__}") from e
    log_event(db, "profile", f"Resume uploaded: {resume_label(rec)} ({rec.original_filename})", commit=True)
    return _profile_payload(db)


class ResumePatch(BaseModel):
    label: str | None = Field(None, max_length=200)
    included: bool | None = None


@router.patch("/resumes/{resume_id}")
def update_resume(resume_id: int, body: ResumePatch, db: Session = Depends(get_db)):
    r = db.get(MasterResume, resume_id)
    if not r:
        raise HTTPException(404, "Resume not found")
    if body.label is not None:
        r.label = body.label.strip()
    if body.included is not None:
        r.is_active = body.included
    db.commit()
    return _profile_payload(db)


@router.delete("/resumes/{resume_id}")
def delete_resume(resume_id: int, db: Session = Depends(get_db)):
    r = db.get(MasterResume, resume_id)
    if not r:
        raise HTTPException(404, "Resume not found")
    Path(r.storage_path).unlink(missing_ok=True)
    label = resume_label(r)
    db.delete(r)
    db.commit()
    log_event(db, "privacy", f"Resume deleted: {label}", commit=True)
    return _profile_payload(db)


@router.post("/resume/draft")
def draft_profile_from_resume(db: Session = Depends(get_db)):
    """Explicit opt-in: sends the text of all included resumes and your notes (email/phone redacted) to the
    configured model, then merges the result into your current profile as a draft for review (not saved)."""
    current = load_profile()
    resumes = included_resumes(db)
    docs = [(f"resume: {resume_label(r)}", r.text) for r in resumes]
    docs += [(f"note: {n.title or n.id}", n.text) for n in current.context_notes]
    if not docs:
        raise HTTPException(400, "Upload at least one resume or add context notes first")
    llm = LLMService(db)
    try:
        system, prompt = profile_draft_prompt(docs)
        draft, meta = llm.structured(task="profile_draft", system=system, prompt=prompt, schema=ProfileDraft,
                                     tier="quality", prompt_version=PROMPT_VERSION_PROFILE, max_output_tokens=8192)
    except LLMError as e:
        raise HTTPException(503, str(e)) from e
    merged = merge_draft(current, draft)
    added_projects = len(merged.projects) - len(current.projects)
    added_bullets = len(merged.all_bullets()) - len(current.all_bullets())
    # Returned as a draft only: you review and save it explicitly.
    header = (f"# DRAFT built from {len(resumes)} resume(s) and {len(current.context_notes)} note(s) by {meta['model']}.\n"
              f"# Adds {added_projects} project(s) and {added_bullets} bullet(s); nothing you wrote was removed.\n"
              "# Review every line against your real experience before saving.\n")
    log_event(db, "profile", f"AI profile draft generated from {len(docs)} document(s) ({meta['model']})", commit=True)
    return {"yaml": header + yaml.safe_dump(merged.model_dump(), sort_keys=False, allow_unicode=True),
            "model": meta["model"], "added_projects": added_projects, "added_bullets": added_bullets}


@router.delete("/data")
def delete_data(scope: str = Query(..., pattern="^(resume|documents|profile|all)$"), db: Session = Depends(get_db)):
    s = get_settings()
    removed = []
    if scope in ("resume", "all"):
        for r in db.scalars(select(MasterResume)):
            Path(r.storage_path).unlink(missing_ok=True)
        db.execute(delete(MasterResume))
        removed.append("master resumes")
    if scope in ("documents", "all"):
        if scope == "all":
            db.execute(delete(Application))
        else:
            for app in db.scalars(select(Application)):
                app.resume_document_id = app.cover_document_id = None
                app.approved_at, app.approval_hash = None, ""
        db.execute(delete(Document))
        shutil.rmtree(s.documents_dir, ignore_errors=True)
        s.documents_dir.mkdir(parents=True, exist_ok=True)
        for job in db.scalars(select(Job).where(Job.pipeline_status.in_(["resume_ready", "ready_for_review", "approved"]))):
            job.pipeline_status = "shortlisted"
        removed.append("generated documents" + (" and application records" if scope == "all" else ""))
    if scope in ("profile", "all"):
        s.profile_path.unlink(missing_ok=True)
        removed.append("profile (reset to template)")
    db.commit()
    log_event(db, "privacy", "Deleted: " + ", ".join(removed), commit=True)
    return {"removed": removed}
