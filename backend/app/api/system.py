from __future__ import annotations

import threading
from datetime import timedelta

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .. import auth
from ..config import Settings, get_settings
from ..core.text import dumps, loads
from ..core.yaml_config import SearchConfig, load_profile, load_search_config, parse_yaml_text, save_search_config
from ..db import get_db, session_factory
from ..llm.service import LLMService
from ..models import Event, Job, LlmUsage, Run, aware, utcnow
from ..services.activity import log_event
from ..services.notify import attention_items, send_daily_digest
from ..services.pipeline import discover, rank_jobs, retier_all, run_daily
from ..sources.http import PoliteClient
from .serializers import iso, job_summary

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_auth)])
_run_lock = threading.Lock()


# ------------------------------------------------------------------ public

class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=500)


@public.get("/health")
def health():
    return {"status": "ok"}


@public.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response, s: Settings = Depends(get_settings)):
    auth.login(response, body.password, request, s)
    return {"authenticated": True}


@public.post("/auth/logout")
def logout(response: Response):
    auth.logout(response)
    return {"authenticated": False}


@public.get("/auth/me")
def me(request: Request, s: Settings = Depends(get_settings)):
    return {"authenticated": auth.is_authenticated(request, s), "password_configured": bool(s.app_password)}


@public.post("/internal/run-daily")
def scheduled_daily_run(request: Request, s: Settings = Depends(get_settings)):
    """Daily pipeline triggered by Cloud Scheduler. Runs synchronously so the (single) Cloud Run instance stays
    alive until it finishes. Only accepts a Google-signed OIDC token for the configured scheduler account."""
    if not s.cron_invoker_email:
        raise HTTPException(404)
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    audience = s.public_base_url.rstrip("/") + "/api/internal/run-daily"
    try:
        claims = id_token.verify_oauth2_token(token, g_requests.Request(), audience=audience)
    except Exception as e:
        raise HTTPException(401, "Invalid scheduler token") from e
    if claims.get("email") != s.cron_invoker_email or not claims.get("email_verified"):
        raise HTTPException(403, "Caller not allowed")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "A run is already in progress")
    db = session_factory()()
    try:
        run = run_daily(db, notify=True)
        return {"run": run.id, "status": run.status}
    finally:
        db.close()
        _run_lock.release()


# ------------------------------------------------------------------ overview

@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    cfg = load_search_config()
    since = utcnow() - timedelta(hours=24)
    base = select(func.count(Job.id)).where(Job.duplicate_of_id.is_(None))
    open_q = base.where(Job.listing_status == "open")
    tiers = dict(db.execute(select(Job.tier, func.count(Job.id)).where(Job.duplicate_of_id.is_(None), Job.listing_status == "open")
                            .group_by(Job.tier)).all())
    pipeline = dict(db.execute(select(Job.pipeline_status, func.count(Job.id)).where(Job.duplicate_of_id.is_(None))
                               .group_by(Job.pipeline_status)).all())
    top = db.scalars(select(Job).where(Job.duplicate_of_id.is_(None), Job.listing_status == "open", Job.hard_excluded.is_(False),
                                       Job.tier.in_(["A", "B"]), Job.pipeline_status.notin_(["archived", "rejected"]))
                     .order_by(Job.rank_value.desc()).limit(8)).all()
    fresh = db.scalars(select(Job).where(Job.duplicate_of_id.is_(None), Job.first_seen_at >= since, Job.hard_excluded.is_(False),
                                         Job.listing_status == "open")
                       .order_by(Job.rank_value.desc()).limit(6)).all()
    last_run = db.scalars(select(Run).order_by(Run.id.desc())).first()
    leads = db.scalars(select(Job).where(Job.duplicate_of_id.is_(None), Job.listing_status == "unverified",
                                         Job.pipeline_status == "discovered")
                       .order_by(Job.first_seen_at.desc()).limit(8)).all()
    manual_watch = [{"name": c.name, "careers_url": c.careers_url, "notes": c.notes}
                    for c in cfg.companies if not any(s.enabled for s in c.sources) and c.careers_url]
    return {
        "counts": {
            "open": db.scalar(open_q) or 0, "new_24h": db.scalar(base.where(Job.first_seen_at >= since)) or 0,
            "closed": db.scalar(base.where(Job.listing_status == "closed")) or 0,
            "duplicates": db.scalar(select(func.count(Job.id)).where(Job.duplicate_of_id.is_not(None))) or 0,
            "excluded": db.scalar(open_q.where(Job.hard_excluded.is_(True))) or 0,
            "leads": db.scalar(base.where(Job.listing_status == "unverified")) or 0,
        },
        "tiers": tiers, "pipeline": pipeline,
        "top": [job_summary(j) for j in top], "fresh": [job_summary(j) for j in fresh],
        "leads": [job_summary(j) for j in leads],
        "attention": attention_items(db),
        "last_run": _run_dict(last_run) if last_run else None, "run_in_progress": _run_lock.locked(),
        "llm": LLMService(db).budget_status(), "manual_watch": manual_watch,
    }


def _run_dict(r: Run) -> dict:
    return {"id": r.id, "kind": r.kind, "status": r.status, "started_at": iso(r.started_at),
            "finished_at": iso(r.finished_at), "stats": loads(r.stats_json, {})}


# ------------------------------------------------------------------ settings

def _settings_payload() -> dict:
    s = get_settings()
    cfg = load_search_config()
    return {
        "config": cfg.model_dump(), "yaml": s.search_config_path.read_text(encoding="utf-8"),
        "environment": {
            "llm_provider": s.llm_provider, "gcp_project_set": bool(s.google_cloud_project),
            "gcp_location": s.google_cloud_location, "model_fast": s.gemini_model_fast, "model_quality": s.gemini_model_quality,
            "llm_daily_call_limit": s.llm_daily_call_limit, "llm_daily_budget_usd": s.llm_daily_budget_usd,
            "email_configured": bool(s.smtp_host and s.notify_email_to),
            "telegram_configured": bool(s.telegram_bot_token and s.telegram_chat_id),
            "browser_automation_enabled": s.browser_automation_enabled,
            "database": "postgresql" if s.resolved_database_url.startswith("postgres") else "sqlite",
        },
    }


@router.get("/settings")
def get_search_settings():
    return _settings_payload()


class SettingsUpdate(BaseModel):
    yaml: str | None = Field(None, max_length=300000)
    config: dict | None = None


@router.put("/settings")
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)):
    try:
        if body.yaml is not None:
            cfg = parse_yaml_text(body.yaml, SearchConfig)
        elif body.config is not None:
            cfg = SearchConfig.model_validate(body.config)
        else:
            raise HTTPException(400, "Provide yaml or config")
    except (yaml.YAMLError, ValidationError, ValueError) as e:
        raise HTTPException(422, f"Invalid settings: {str(e)[:800]}") from e
    save_search_config(cfg)
    retier_all(db, cfg)
    log_event(db, "settings", "Search settings updated (jobs will be re-scored on next ranking run)")
    db.commit()
    return _settings_payload()


# ------------------------------------------------------------------ runs

def _background(kind: str, fn) -> None:
    def target():
        db = session_factory()()
        try:
            fn(db)
        except Exception as e:  # already logged inside pipeline where possible
            log_event(db, "pipeline", f"{kind} failed: {type(e).__name__}: {e}"[:1000], level="error", commit=True)
        finally:
            db.close()
            _run_lock.release()

    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "A run is already in progress")
    threading.Thread(target=target, daemon=True).start()


@router.post("/runs/daily")
def start_daily(notify: bool = False):
    _background("daily", lambda db: run_daily(db, notify=notify))
    return {"started": True}


def maybe_auto_refresh(hours: float) -> bool:
    """Start a background daily run if the last successful one is older than `hours` (used at startup)."""
    if hours <= 0:
        return False
    db = session_factory()()
    try:
        last = db.scalars(select(Run).where(Run.kind == "daily", Run.status.in_(["success", "partial"]))
                          .order_by(Run.started_at.desc())).first()
        if last and last.started_at and aware(last.started_at) > utcnow() - timedelta(hours=hours):
            return False
        log_event(db, "pipeline", f"Auto-refresh: last daily run is older than {hours:g} h — starting one", commit=True)
    finally:
        db.close()
    try:
        _background("daily", lambda db: run_daily(db, notify=True))
    except HTTPException:
        return False
    return True


@router.post("/runs/discover")
def start_discover(company: str | None = None, web: bool = True):
    def fn(db):
        run = Run(kind="discover")
        db.add(run)
        db.commit()
        http = PoliteClient()
        try:
            st = discover(db, load_search_config(), http, run.id, only_company=company, llm=LLMService(db), web_search=web)
            st.pop("new_job_ids", None)
            st["ranking"] = rank_jobs(db, load_search_config(), load_profile(), LLMService(db), use_ai=False)
            run.status = "partial" if st["errors"] else "success"
            run.stats_json = dumps(st)
        finally:
            http.close()
            run.finished_at = utcnow()
            db.commit()
    _background("discover", fn)
    return {"started": True}


@router.post("/runs/rank")
def start_rank(use_ai: bool = True, force: bool = False):
    def fn(db):
        run = Run(kind="rank")
        db.add(run)
        db.commit()
        st = rank_jobs(db, load_search_config(), load_profile(), LLMService(db), use_ai=use_ai, force=force, run_id=run.id)
        run.status, run.stats_json, run.finished_at = "success", dumps(st), utcnow()
        db.commit()
    _background("rank", fn)
    return {"started": True}


@router.get("/runs")
def list_runs(limit: int = Query(30, le=200), db: Session = Depends(get_db)):
    return {"items": [_run_dict(r) for r in db.scalars(select(Run).order_by(Run.id.desc()).limit(limit))],
            "in_progress": _run_lock.locked()}


@router.get("/events")
def list_events(level: str = "", category: str = "", limit: int = Query(200, le=1000), db: Session = Depends(get_db)):
    q = select(Event).order_by(Event.id.desc()).limit(limit)
    if level:
        q = q.where(Event.level.in_(level.split(",")))
    if category:
        q = q.where(Event.category == category)
    return {"items": [{"id": e.id, "at": iso(e.created_at), "level": e.level, "category": e.category,
                       "message": e.message, "job_id": e.job_id, "run_id": e.run_id, "data": loads(e.data_json, {})}
                      for e in db.scalars(q)]}


@router.get("/usage")
def usage(days: int = Query(14, le=90), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(days=days)
    day = func.date(LlmUsage.created_at)
    rows = db.execute(select(day, LlmUsage.task, LlmUsage.model, func.count(LlmUsage.id),
                             func.sum(LlmUsage.input_tokens), func.sum(LlmUsage.output_tokens), func.sum(LlmUsage.est_cost_usd),
                             func.sum(case((LlmUsage.cached.is_(True), 1), else_=0)),
                             func.sum(case((LlmUsage.success.is_(False), 1), else_=0)))
                      .where(LlmUsage.created_at >= since)
                      .group_by(day, LlmUsage.task, LlmUsage.model)
                      .order_by(day.desc())).all()
    items = [{"day": str(d), "task": t, "model": m, "calls": c, "input_tokens": i or 0, "output_tokens": o or 0,
              "est_cost_usd": round(cost or 0, 5), "cached": ca or 0, "failed": f or 0} for d, t, m, c, i, o, cost, ca, f in rows]
    total = sum(x["est_cost_usd"] for x in items)
    return {"items": items, "total_est_cost_usd": round(total, 4), "budget": LLMService(db).budget_status(),
            "note": "Estimates use configured per-token prices; your Google Cloud bill is authoritative."}


@router.post("/notify/preview")
def notify_preview(db: Session = Depends(get_db)):
    return send_daily_digest(db, load_search_config(), dry_run=True)


@router.post("/notify/send")
def notify_send(db: Session = Depends(get_db)):
    return send_daily_digest(db, load_search_config())
