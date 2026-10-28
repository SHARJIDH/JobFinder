"""Application workspace, approval gate and evidence-based status tracking."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.safety import detect_injection
from ..core.states import TransitionError, check_transition
from ..core.text import dumps, loads, stable_json_hash
from ..core.yaml_config import Profile
from ..llm.service import LLMError, LLMService
from ..llm.tasks import PROMPT_VERSION_ANSWERS, DraftAnswers, answers_prompt, candidate_facts
from ..models import Application, Document, Job, utcnow
from .activity import log_event
from .profile_facts import evidence, usable_skills

CHECKLIST = {
    "official_page_checked": "Opened the official posting and confirmed it is still open",
    "resume_reviewed": "Reviewed the tailored resume for accuracy",
    "cover_reviewed": "Reviewed the cover letter (or decided not to use one)",
    "answers_verified": "Verified every application answer",
    "eligibility_confirmed": "Confirmed location / experience / work-authorization eligibility",
}

FACT_QUESTIONS = [
    ("work_authorization_india", "Are you legally authorized to work in India?"),
    ("requires_visa_sponsorship", "Will you now or in the future require visa sponsorship?"),
    ("notice_period", "What is your notice period?"),
    ("current_ctc", "What is your current CTC?"),
    ("expected_ctc", "What is your expected CTC?"),
    ("willing_to_relocate", "Are you willing to relocate?"),
    ("earliest_start_date", "What is your earliest start date?"),
]


def set_status(db: Session, job: Job, target: str, note: str = "", app: Application | None = None) -> None:
    check_transition(job.pipeline_status, target)
    prev = job.pipeline_status
    job.pipeline_status = target
    app = app or db.scalars(select(Application).where(Application.job_id == job.id)).first()
    if app is not None:
        hist = loads(app.history_json, [])
        hist.append({"at": utcnow().isoformat(), "from": prev, "to": target, "note": note[:500]})
        app.history_json = dumps(hist)
    log_event(db, "workflow", f"{job.company} — {job.title}: {prev} → {target}" + (f" ({note})" if note else ""), job_id=job.id)


def advance_to(db: Session, job: Job, target: str, note: str = "") -> None:
    """Walk forward through intermediate states (e.g. discovered → shortlisted → resume_ready)."""
    order = ["discovered", "shortlisted", "resume_ready", "ready_for_review"]
    if job.pipeline_status in order and target in order:
        for st in order[order.index(job.pipeline_status) + 1 : order.index(target) + 1]:
            set_status(db, job, st, note)
    elif job.pipeline_status != target:
        set_status(db, job, target, note)


def _fact_answers(profile: Profile) -> list[dict]:
    out = []
    facts = profile.application_facts.model_dump()
    for key, q in FACT_QUESTIONS:
        val = (facts.get(key) or "").strip()
        out.append({"key": key, "question": q, "answer": val, "source": "profile" if val else "needs_input",
                    "needs_input": not val, "verified": bool(val), "include": True})
    c = profile.contact
    for key, q, val in (("linkedin", "LinkedIn profile", c.linkedin), ("github", "GitHub profile", c.github),
                        ("portfolio", "Portfolio / website", c.portfolio)):
        if val:
            out.append({"key": key, "question": q, "answer": val, "source": "profile", "needs_input": False,
                        "verified": True, "include": True})
    for k, v in profile.extra_answers.items():
        out.append({"key": f"extra:{k}", "question": k, "answer": v, "source": "profile", "needs_input": False,
                    "verified": True, "include": True})
    return out


def _narrative_answers(db: Session, job: Job, profile: Profile, llm: LLMService | None) -> tuple[list[dict], list[str]]:
    questions = [f"Why are you interested in this role at {job.company}?",
                 "Describe a project or experience most relevant to this role."]
    warnings: list[str] = []
    drafts: dict[str, str] = {}
    if llm and llm.available:
        facts = candidate_facts(profile, usable_skills(profile, evidence(db, profile).resume_text))
        try:
            system, prompt = answers_prompt(job, facts, questions)
            out, _ = llm.structured(task="answers", system=system, prompt=prompt, schema=DraftAnswers, tier="fast",
                                    prompt_version=PROMPT_VERSION_ANSWERS, max_output_tokens=1200)
            for a in out.answers:
                if not a.needs_candidate_input and a.answer.strip():
                    drafts[a.question.strip()] = a.answer.strip()[:1500]
        except LLMError as e:
            warnings.append(f"AI answer drafting unavailable: {e}")
    res = []
    for i, q in enumerate(questions):
        ans = drafts.get(q, "")
        res.append({"key": f"narrative{i}", "question": q, "answer": ans, "source": "ai_draft" if ans else "needs_input",
                    "needs_input": not ans, "verified": False, "include": True})
    return res, warnings


def compute_concerns(job: Job) -> list[str]:
    bd = loads(job.score_breakdown_json, {}) or {}
    out = [f"Hard exclusion: {r}" for r in loads(job.exclusion_reasons_json, [])]
    out += [f"Flag: {f}" for f in bd.get("flags", [])]
    missing = bd.get("skills", {}).get("missing_required", [])
    if missing:
        out.append("Required skills without verified evidence: " + ", ".join(missing))
    ai = loads(job.ai_analysis_json, {}) or {}
    out += [f"AI: {c}" for c in ai.get("missing_mandatory_qualifications", [])]
    out += [f"AI: {c}" for c in ai.get("eligibility_concerns", [])]
    if job.listing_status != "open":
        out.append(f"Listing status: {job.listing_status} {job.closed_reason}".strip())
    inj = detect_injection(job.description)
    if inj:
        out.append("Job description contains instruction-like text (treated as data): " + "; ".join(inj[:3]))
    return out


def prepare_application(db: Session, job: Job, profile: Profile, llm: LLMService | None,
                        resume_doc: Document | None, cover_doc: Document | None) -> tuple[Application, list[str]]:
    if job.pipeline_status not in ("discovered", "shortlisted", "resume_ready", "ready_for_review", "approved"):
        raise TransitionError(f"Cannot prepare an application in status '{job.pipeline_status}'")
    app = db.scalars(select(Application).where(Application.job_id == job.id)).first()
    if app is None:
        app = Application(job_id=job.id, checklist_json=dumps({k: False for k in CHECKLIST}))
        db.add(app)
        db.flush()
    narrative, warnings = _narrative_answers(db, job, profile, llm)
    existing = {a["key"]: a for a in loads(app.answers_json, [])}
    answers = []
    for a in _fact_answers(profile) + narrative:
        prev = existing.get(a["key"])
        answers.append(prev if prev and prev.get("verified") else a)  # keep user-verified edits
    app.answers_json = dumps(answers)
    app.concerns_json = dumps(compute_concerns(job))
    if resume_doc:
        app.resume_document_id = resume_doc.id
    if cover_doc:
        app.cover_document_id = cover_doc.id
    revoke_approval(db, job, app, "workspace regenerated")
    if job.pipeline_status != "ready_for_review":
        advance_to(db, job, "ready_for_review", "application workspace prepared")
    return app, warnings


def snapshot_hash(db: Session, app: Application) -> str:
    docs = [db.get(Document, i) for i in (app.resume_document_id, app.cover_document_id) if i]
    return stable_json_hash({
        "answers": [a for a in loads(app.answers_json, []) if a.get("include")],
        "docs": [(d.id, d.content_json, d.tex_source) for d in docs if d],
    })


def revoke_approval(db: Session, job: Job, app: Application, reason: str) -> None:
    if app.approved_at is None:
        return
    app.approved_at, app.approval_hash = None, ""
    if job.pipeline_status == "approved":
        set_status(db, job, "ready_for_review", f"approval revoked: {reason}", app)


class ApprovalError(ValueError):
    pass


def approval_blockers(db: Session, app: Application, job: Job) -> list[str]:
    blockers = []
    if job.pipeline_status != "ready_for_review":
        blockers.append(f"Application must be in 'Ready for Review' (currently '{job.pipeline_status}')")
    if not app.resume_document_id:
        blockers.append("No tailored resume attached")
    checklist = loads(app.checklist_json, {})
    blockers += [f"Checklist: {label}" for k, label in CHECKLIST.items() if not checklist.get(k)]
    for a in loads(app.answers_json, []):
        if not a.get("include"):
            continue
        if a.get("needs_input") or not str(a.get("answer", "")).strip():
            blockers.append(f"Answer needed: {a['question']}")
        elif not a.get("verified"):
            blockers.append(f"Answer not verified: {a['question']}")
    if job.listing_status == "closed":
        blockers.append("Listing appears closed")
    return blockers


def approve(db: Session, app: Application, job: Job) -> None:
    blockers = approval_blockers(db, app, job)
    if blockers:
        raise ApprovalError("; ".join(blockers))
    app.approved_at = utcnow()
    app.approval_hash = snapshot_hash(db, app)
    set_status(db, job, "approved", "explicitly approved by user", app)


def record_submission(db: Session, app: Application, job: Job, evidence: str, method: str) -> None:
    """Mark submitted only with explicit approval of the current snapshot and user-provided evidence."""
    if job.pipeline_status != "approved" or not app.approved_at:
        raise ApprovalError("Submission can only be recorded for an approved application")
    if app.approval_hash != snapshot_hash(db, app):
        raise ApprovalError("Documents or answers changed after approval; re-approve first")
    if len((evidence or "").strip()) < 8:
        raise ApprovalError("Provide submission evidence (confirmation message, email subject, or reference id)")
    if method not in ("manual_official_site", "browser_assist_user_clicked_submit"):
        raise ApprovalError("Unknown submission method")
    app.submitted_at = utcnow()
    app.submission_method = method
    app.submission_evidence = evidence.strip()[:4000]
    set_status(db, job, "submitted", f"evidence recorded ({method})", app)


__all__ = ["TransitionError", "CHECKLIST", "prepare_application", "approve", "record_submission",
           "approval_blockers", "revoke_approval", "set_status", "advance_to", "snapshot_hash", "ApprovalError"]
