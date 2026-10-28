from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import get_settings
from ..core.states import LABELS, TransitionError
from ..core.text import dumps, loads
from ..core.yaml_config import load_profile
from ..db import get_db
from ..models import Application, Document, Job
from ..services.activity import log_event
from ..services.applications import (
    CHECKLIST,
    ApprovalError,
    approval_blockers,
    approve,
    record_submission,
    revoke_approval,
    set_status,
)
from .serializers import application_dict, document_dict, job_summary

router = APIRouter(prefix="/api/applications", dependencies=[Depends(require_auth)])


def _load(db: Session, app_id: int) -> tuple[Application, Job]:
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Application not found")
    return app, db.get(Job, app.job_id)


@router.get("")
def list_applications(db: Session = Depends(get_db)):
    rows = db.execute(select(Application, Job).join(Job, Job.id == Application.job_id).order_by(Application.updated_at.desc())).all()
    # also include shortlisted / resume-ready jobs that don't have a workspace yet
    loose = db.scalars(select(Job).where(Job.pipeline_status.in_(["shortlisted", "resume_ready"]),
                                         Job.id.notin_([a.job_id for a, _ in rows] or [-1]))).all()
    items = []
    for app, job in rows:
        answers = loads(app.answers_json, [])
        items.append({"application_id": app.id, "job": job_summary(job), "approved_at": app.approved_at,
                      "submitted_at": app.submitted_at, "deadline": app.deadline,
                      "needs_input": sum(1 for a in answers if a.get("include") and a.get("needs_input"))})
    items += [{"application_id": None, "job": job_summary(j), "approved_at": None, "submitted_at": None,
               "deadline": None, "needs_input": 0} for j in loose]
    return {"items": items, "labels": LABELS}


@router.get("/{app_id}")
def get_application(app_id: int, db: Session = Depends(get_db)):
    app, job = _load(db, app_id)
    docs = {k: db.get(Document, i) for k, i in (("resume", app.resume_document_id), ("cover_letter", app.cover_document_id)) if i}
    return {
        "application": application_dict(app), "job": job_summary(job),
        "documents": {k: document_dict(v, True) for k, v in docs.items() if v},
        "checklist_labels": CHECKLIST, "blockers": approval_blockers(db, app, job),
        "browser_automation_enabled": get_settings().browser_automation_enabled,
    }


class Answer(BaseModel):
    key: str = Field(max_length=100)
    question: str = Field(max_length=500)
    answer: str = Field("", max_length=4000)
    source: str = "user"
    needs_input: bool = False
    verified: bool = False
    include: bool = True


class UpdateBody(BaseModel):
    answers: list[Answer] | None = None
    checklist: dict[str, bool] | None = None
    notes: str | None = Field(None, max_length=10000)
    deadline: datetime | None = None


@router.put("/{app_id}")
def update_application(app_id: int, body: UpdateBody, db: Session = Depends(get_db)):
    app, job = _load(db, app_id)
    if job.pipeline_status not in ("ready_for_review", "approved", "resume_ready", "shortlisted") and (body.answers or body.checklist):
        raise HTTPException(409, f"Cannot edit answers in status '{job.pipeline_status}'")
    changed_snapshot = False
    if body.answers is not None:
        cleaned = []
        for a in body.answers:
            d = a.model_dump()
            d["needs_input"] = not d["answer"].strip()
            if d["source"] not in ("profile", "ai_draft", "user", "needs_input"):
                d["source"] = "user"
            cleaned.append(d)
        app.answers_json = dumps(cleaned)
        changed_snapshot = True
    if body.checklist is not None:
        cur = loads(app.checklist_json, {})
        cur.update({k: bool(v) for k, v in body.checklist.items() if k in CHECKLIST})
        app.checklist_json = dumps(cur)
    if body.notes is not None:
        app.notes = body.notes
    if body.deadline is not None:
        app.deadline = body.deadline
    if changed_snapshot:
        revoke_approval(db, job, app, "answers edited")
    db.commit()
    return get_application(app_id, db)


@router.post("/{app_id}/approve")
def approve_application(app_id: int, db: Session = Depends(get_db)):
    app, job = _load(db, app_id)
    try:
        approve(db, app, job)
    except (ApprovalError, TransitionError) as e:
        raise HTTPException(409, str(e)) from e
    db.commit()
    return get_application(app_id, db)


@router.post("/{app_id}/revoke")
def revoke(app_id: int, db: Session = Depends(get_db)):
    app, job = _load(db, app_id)
    revoke_approval(db, job, app, "revoked by user")
    db.commit()
    return get_application(app_id, db)


class SubmittedBody(BaseModel):
    evidence: str = Field(min_length=8, max_length=4000)
    method: str = "manual_official_site"


@router.post("/{app_id}/submitted")
def mark_submitted(app_id: int, body: SubmittedBody, db: Session = Depends(get_db)):
    app, job = _load(db, app_id)
    try:
        record_submission(db, app, job, body.evidence, body.method)
    except (ApprovalError, TransitionError) as e:
        log_event(db, "workflow", f"Rejected submission record for job {job.id}: {e}", level="warn", job_id=job.id, commit=True)
        raise HTTPException(409, str(e)) from e
    db.commit()
    return get_application(app_id, db)


class OutcomeBody(BaseModel):
    status: str
    note: str = Field("", max_length=2000)


@router.post("/{app_id}/outcome")
def outcome(app_id: int, body: OutcomeBody, db: Session = Depends(get_db)):
    app, job = _load(db, app_id)
    if body.status not in ("interview", "offer", "rejected", "closed", "archived"):
        raise HTTPException(400, "Outcome must be interview, offer, rejected, closed or archived")
    try:
        set_status(db, job, body.status, body.note or "recorded by user", app)
    except TransitionError as e:
        raise HTTPException(409, str(e)) from e
    db.commit()
    return get_application(app_id, db)


@router.post("/{app_id}/fill")
def assisted_fill(app_id: int, db: Session = Depends(get_db)):
    s = get_settings()
    if not s.browser_automation_enabled:
        raise HTTPException(400, "Browser automation is disabled (set BROWSER_AUTOMATION_ENABLED=true; local use only)")
    app, job = _load(db, app_id)
    if job.pipeline_status not in ("ready_for_review", "approved"):
        raise HTTPException(409, "Prepare the application workspace first")
    from ..services.browser_fill import is_supported, launch_assisted_fill

    if not is_supported(job.apply_url):
        raise HTTPException(400, "Assisted filling supports Greenhouse, Lever and Ashby hosted forms only. Open the official link manually.")
    docs = [db.get(Document, i) if i else None for i in (app.resume_document_id, app.cover_document_id)]
    paths = [Path(d.pdf_path) if d and d.pdf_path else None for d in docs]
    try:
        report = launch_assisted_fill(app.id, job.apply_url, load_profile(), loads(app.answers_json, []), paths[0], paths[1])
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    log_event(db, "browser", f"Assisted fill for {job.company} — {job.title}: filled {len(report.filled)}, "
                             f"uploaded {len(report.uploaded)}{'; blocked: ' + report.blocked if report.blocked else ''}",
              job_id=job.id, commit=True)
    return report.__dict__
