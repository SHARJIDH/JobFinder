import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="jh-test-"))
os.environ.update({
    "APP_ENV": "test", "DATA_DIR": str(_TMP), "APP_PASSWORD": "test-pass", "SESSION_SECRET": "test-secret",
    "LLM_PROVIDER": "none", "DATABASE_URL": "", "SMTP_HOST": "", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "",
    "NOTIFY_EMAIL_TO": "", "BROWSER_AUTOMATION_ENABLED": "false", "HTTP_MIN_INTERVAL_SECONDS": "0",
    "RESUME_FORMAT": "classic",
})

from app.config import get_settings  # noqa: E402
from app.db import Base, configure_engine, session_factory  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def fresh_env():
    get_settings.cache_clear()
    s = get_settings()
    for p in (s.profile_path, s.search_config_path):
        p.unlink(missing_ok=True)
    shutil.rmtree(s.documents_dir, ignore_errors=True)
    shutil.rmtree(s.uploads_dir, ignore_errors=True)
    s.ensure_dirs()
    db_path = s.data_dir / "test.db"
    db_path.unlink(missing_ok=True)
    pg_url = os.environ.get("TEST_DATABASE_URL")  # e.g. run the suite against a throwaway Postgres
    engine = configure_engine(pg_url or f"sqlite:///{db_path}")
    from app import models  # noqa: F401

    if pg_url:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture
def db():
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    c = TestClient(create_app())
    r = c.post("/api/auth/login", json={"password": "test-pass"})
    assert r.status_code == 200
    c.headers.update({"X-Requested-With": "jobhunter"})
    return c


@pytest.fixture
def profile_yaml():
    """A realistic, clearly fictional test profile."""
    return (FIXTURES / "profile_test.yaml").read_text()


@pytest.fixture
def test_profile(profile_yaml):
    from app.core.yaml_config import Profile, parse_yaml_text, save_profile

    p = parse_yaml_text(profile_yaml, Profile)
    save_profile(p)
    return p
