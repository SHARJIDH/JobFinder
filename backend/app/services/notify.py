"""Daily shortlist notifications via email (SMTP) and/or Telegram."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from html import escape

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..core.text import dumps, loads
from ..core.yaml_config import SearchConfig
from ..models import Application, Job, Notification, utcnow
from .activity import log_event

TIER_ORDER = {"A": 0, "B": 1, "C": 2}


def digest_jobs(db: Session, cfg: SearchConfig, include_notified: bool = False) -> list[Job]:
    max_tier = TIER_ORDER[cfg.limits.notify_min_tier]
    q = select(Job).where(Job.duplicate_of_id.is_(None), Job.listing_status == "open", Job.hard_excluded.is_(False),
                          Job.tier.in_([t for t, o in TIER_ORDER.items() if o <= max_tier]),
                          Job.pipeline_status.notin_(["archived", "closed", "rejected", "submitted"]))
    if not include_notified:
        q = q.where(Job.notified_at.is_(None))
    return list(db.scalars(q.order_by(Job.rank_value.desc()).limit(cfg.limits.notify_max_jobs)))


def attention_items(db: Session) -> list[str]:
    items = []
    rows = db.execute(select(Application, Job).join(Job, Job.id == Application.job_id)
                      .where(Job.pipeline_status.in_(["ready_for_review", "approved"]))).all()
    for app, job in rows:
        needs = [a["question"] for a in loads(app.answers_json, []) if a.get("include") and a.get("needs_input")]
        if needs:
            items.append(f"{job.company} — {job.title}: needs your input on {len(needs)} answer(s)")
        elif job.pipeline_status == "ready_for_review":
            items.append(f"{job.company} — {job.title}: ready for your review")
        else:
            items.append(f"{job.company} — {job.title}: approved; submit on the official site and record evidence")
    return items


def _salary_label(job: Job) -> str:
    bd = loads(job.score_breakdown_json, {}) or {}
    lo, hi = (bd.get("annual_lpa") or [None, None])[:2]
    if lo is None and hi is None:
        return "salary not disclosed"
    kind = {"base": "base", "total": "total comp", "estimated": "est."}.get(job.salary_kind, job.salary_kind)
    return f"₹{lo}–{hi} LPA {kind}"


def format_digest(jobs: list[Job], attention: list[str], base_url: str) -> tuple[str, str, str]:
    subject = f"AI Job Hunter: {len(jobs)} new match(es)" + (f", {len(attention)} need attention" if attention else "")
    lines, html = [], ["<h2>Today's strongest matches</h2><ol>"]
    for j in jobs:
        posted = j.posted_at.date().isoformat() if j.posted_at else "date unverified"
        lines.append(f"[{j.tier}] {j.score:.0f} — {j.title} @ {j.company} ({j.location}) · {_salary_label(j)} · posted {posted}\n"
                     f"   Apply (official): {j.apply_url}\n   Details: {base_url}/jobs/view?id={j.id}")
        html.append(f"<li><b>[{escape(j.tier)}] {j.score:.0f}</b> — {escape(j.title)} @ <b>{escape(j.company)}</b> "
                    f"({escape(j.location)}) · {escape(_salary_label(j))} · posted {escape(posted)}<br>"
                    f"<a href='{escape(j.apply_url)}'>Official posting</a> · "
                    f"<a href='{escape(base_url)}/jobs/view?id={j.id}'>Match details</a></li>")
    html.append("</ol>")
    if not jobs:
        lines.append("No new strong matches today.")
        html.append("<p>No new strong matches today.</p>")
    if attention:
        lines.append("\nNeeds your attention:")
        lines += [f" - {a}" for a in attention]
        html.append("<h3>Needs your attention</h3><ul>" + "".join(f"<li>{escape(a)}</li>" for a in attention) + "</ul>")
    return subject, "\n".join(lines), "".join(html)


def send_email(s: Settings, subject: str, text: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, s.notify_email_from or s.smtp_username, s.notify_email_to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if s.smtp_username:
            smtp.login(s.smtp_username, s.smtp_password)
        smtp.send_message(msg)


def send_telegram(s: Settings, text: str, client: httpx.Client | None = None) -> None:
    c = client or httpx.Client(timeout=20)
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)] or [text]:
        r = c.post(f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                   json={"chat_id": s.telegram_chat_id, "text": chunk, "disable_web_page_preview": True})
        if r.status_code != 200:
            raise RuntimeError(f"Telegram API returned {r.status_code}")


def send_daily_digest(db: Session, cfg: SearchConfig, settings: Settings | None = None, *, dry_run: bool = False,
                      telegram_client: httpx.Client | None = None) -> dict:
    s = settings or get_settings()
    jobs = digest_jobs(db, cfg)
    attention = attention_items(db)
    subject, text, html = format_digest(jobs, attention, s.public_base_url.rstrip("/"))
    result = {"jobs": [j.id for j in jobs], "attention": attention, "subject": subject, "text": text, "sent": []}
    if dry_run or (not jobs and not attention):
        return result
    channels = []
    if s.smtp_host and s.notify_email_to:
        channels.append("email")
    if s.telegram_bot_token and s.telegram_chat_id:
        channels.append("telegram")
    if not channels:
        log_event(db, "notify", "No notification channel configured (SMTP or Telegram); digest not sent", level="warn")
        return result
    any_ok = False
    for ch in channels:
        try:
            send_email(s, subject, text, html) if ch == "email" else send_telegram(s, subject + "\n\n" + text, telegram_client)
            db.add(Notification(channel=ch, status="sent", job_ids_json=dumps(result["jobs"])))
            result["sent"].append(ch)
            any_ok = True
        except Exception as e:  # network/auth errors are logged, never fatal
            db.add(Notification(channel=ch, status="failed", job_ids_json=dumps(result["jobs"]), error=str(e)[:500]))
            log_event(db, "notify", f"{ch} notification failed: {type(e).__name__}", level="error")
    if any_ok:
        now = utcnow()
        for j in jobs:
            j.notified_at = now
    db.commit()
    return result
