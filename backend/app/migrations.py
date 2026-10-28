"""Programmatic Alembic upgrade (used at startup and by the CLI)."""
from alembic import command
from alembic.config import Config

from .config import BACKEND_DIR, get_settings


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().resolved_database_url.replace("%", "%%"))
    return cfg


def upgrade_db() -> None:
    command.upgrade(alembic_config(), "head")
