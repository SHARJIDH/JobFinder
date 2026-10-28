from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; all stored datetimes are UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("provider", "source_job_id", name="uq_job_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # --- source identity ---
    provider: Mapped[str] = mapped_column(String(40), index=True)
    board: Mapped[str] = mapped_column(String(200), default="")
    source_job_id: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(2000), default="")
    apply_url: Mapped[str] = mapped_column(String(2000), default="")
    canonical_url: Mapped[str] = mapped_column(String(2000), default="", index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)

    # --- posting content ---
    company: Mapped[str] = mapped_column(String(250), index=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    department: Mapped[str] = mapped_column(String(250), default="")
    location: Mapped[str] = mapped_column(String(500), default="")
    locations_json: Mapped[str] = mapped_column(Text, default="[]")
    country: Mapped[str] = mapped_column(String(80), default="")
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    work_mode: Mapped[str] = mapped_column(String(30), default="unknown")
    employment_type: Mapped[str] = mapped_column(String(60), default="")
    experience_text: Mapped[str] = mapped_column(String(500), default="")
    min_years_required: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    required_skills_json: Mapped[str] = mapped_column(Text, default="[]")
    preferred_skills_json: Mapped[str] = mapped_column(Text, default="[]")

    # --- compensation (never fabricated) ---
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(10), default="")
    salary_period: Mapped[str] = mapped_column(String(20), default="")
    salary_kind: Mapped[str] = mapped_column(String(20), default="unknown")  # base|total|estimated|unknown
    salary_source: Mapped[str] = mapped_column(String(40), default="")
    salary_text: Mapped[str] = mapped_column(String(500), default="")

    # --- dates & verification ---
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at_evidence: Mapped[str] = mapped_column(String(200), default="")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    listing_status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|closed|unknown
    closed_reason: Mapped[str] = mapped_column(String(300), default="")

    # --- workflow ---
    pipeline_status: Mapped[str] = mapped_column(String(30), default="discovered", index=True)

    # --- ranking ---
    score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    deterministic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier: Mapped[str] = mapped_column(String(10), default="", index=True)
    rank_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown_json: Mapped[str] = mapped_column(Text, default="{}")
    ai_analysis_json: Mapped[str] = mapped_column(Text, default="")
    hard_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    score_input_hash: Mapped[str] = mapped_column(String(64), default="")
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MasterResume(Base):
    """An uploaded resume. Several can be included at once (e.g. one per specialisation); all included
    resumes are evidence for skills and feed profile building."""
    __tablename__ = "master_resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(200), default="", server_default="")
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def is_latex(self) -> bool:
        return self.content_type == "application/x-tex"

    def text_source(self) -> str:
        """Raw file text (LaTeX source for .tex uploads)."""
        from pathlib import Path

        return Path(self.storage_path).read_text(encoding="utf-8") if self.is_latex else self.text


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # resume|cover_letter
    generation_id: Mapped[str] = mapped_column(String(40), index=True)
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    docx_path: Mapped[str] = mapped_column(String(500), default="")
    pdf_path: Mapped[str] = mapped_column(String(500), default="")
    # LaTeX resumes: editable source, last compile result
    tex_source: Mapped[str] = mapped_column(Text, default="", server_default="")
    tex_origin: Mapped[str] = mapped_column(String(20), default="", server_default="")  # builtin|template|edited
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compile_log: Mapped[str] = mapped_column(Text, default="", server_default="")
    # traceability
    source_resume_id: Mapped[int | None] = mapped_column(ForeignKey("master_resumes.id", ondelete="SET NULL"), nullable=True)
    source_resume_sha256: Mapped[str] = mapped_column(String(64), default="")
    profile_hash: Mapped[str] = mapped_column(String(64), default="")
    job_content_hash: Mapped[str] = mapped_column(String(64), default="")
    generator: Mapped[str] = mapped_column(String(40), default="deterministic")  # deterministic|llm
    model: Mapped[str] = mapped_column(String(80), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    resume_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    cover_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    answers_json: Mapped[str] = mapped_column(Text, default="[]")
    checklist_json: Mapped[str] = mapped_column(Text, default="{}")
    concerns_json: Mapped[str] = mapped_column(Text, default="[]")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_hash: Mapped[str] = mapped_column(String(64), default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submission_method: Mapped[str] = mapped_column(String(40), default="")
    submission_evidence: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    history_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    task: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")


class LlmCache(Base):
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80))
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats_json: Mapped[str] = mapped_column(Text, default="{}")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(10), default="info", index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    job_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    error: Mapped[str] = mapped_column(Text, default="")
