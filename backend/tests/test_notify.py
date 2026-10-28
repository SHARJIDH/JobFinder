import httpx
from sqlalchemy import select

from app.config import get_settings
from app.core.yaml_config import load_search_config
from app.models import Job, Notification
from app.services.notify import send_daily_digest, send_telegram
from app.services.pipeline import rank_jobs
from tests.helpers import ingest, posting


def _ranked(db, test_profile):
    ingest(db, posting(), posting(source_job_id="2", title="Backend Engineer", location="Pune"))
    rank_jobs(db, load_search_config(), test_profile, llm=None)


def test_digest_dry_run_lists_official_links(db, test_profile):
    _ranked(db, test_profile)
    cfg = load_search_config()
    cfg.limits.notify_min_tier = "C"
    res = send_daily_digest(db, cfg, dry_run=True)
    assert res["jobs"] and "boards.greenhouse.io" in res["text"]
    assert all(j.notified_at is None for j in db.scalars(select(Job)))


def test_no_channel_configured_does_not_mark_notified(db, test_profile):
    _ranked(db, test_profile)
    cfg = load_search_config()
    cfg.limits.notify_min_tier = "C"
    res = send_daily_digest(db, cfg)
    assert res["sent"] == []


def test_telegram_send_and_failure_logging(db, test_profile, monkeypatch):
    _ranked(db, test_profile)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    get_settings.cache_clear()
    cfg = load_search_config()
    cfg.limits.notify_min_tier = "C"
    sent = []
    ok_client = httpx.Client(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={"ok": True})))
    res = send_daily_digest(db, cfg, telegram_client=ok_client)
    assert res["sent"] == ["telegram"] and sent
    assert all(j.notified_at is not None for j in db.scalars(select(Job).where(Job.id.in_(res["jobs"]))))
    # second run: nothing new -> nothing sent
    assert send_daily_digest(db, cfg, telegram_client=ok_client)["jobs"] == []


def test_telegram_failure_recorded(db, test_profile, monkeypatch):
    _ranked(db, test_profile)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    get_settings.cache_clear()
    cfg = load_search_config()
    cfg.limits.notify_min_tier = "C"
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    res = send_daily_digest(db, cfg, telegram_client=bad)
    assert res["sent"] == []
    assert db.scalars(select(Notification)).first().status == "failed"
    assert all(j.notified_at is None for j in db.scalars(select(Job)))


def test_send_telegram_chunks_long_messages():
    calls = []
    c = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)))
    s = get_settings().model_copy(update={"telegram_bot_token": "t", "telegram_chat_id": "c"})
    send_telegram(s, "x" * 9000, c)
    assert len(calls) == 3
