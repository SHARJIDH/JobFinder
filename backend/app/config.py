"""Environment-level settings (secrets, infrastructure, limits).

Job-search preferences and the candidate profile live in editable YAML files
(see app/core/yaml_config.py) so they can be changed without code changes.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_DIR / ".env"), str(BACKEND_DIR / ".env")),
        extra="ignore",
    )

    app_env: str = "development"
    data_dir: Path = REPO_DIR / "data"
    database_url: str = ""  # empty -> sqlite file in data_dir

    # --- Access control (single-user) ---
    app_password: str = ""  # required unless app_env == "test"
    session_secret: str = ""
    session_max_age_hours: int = 24 * 7
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --- LLM provider ---
    llm_provider: str = "none"  # "gemini" | "none"
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    gemini_model_fast: str = "gemini-3.1-flash-lite"
    gemini_model_quality: str = "gemini-2.5-flash"
    gemini_model_search: str = "gemini-2.5-flash"  # used with Grounding with Google Search
    # Estimated USD price per 1M tokens. Verify against current Vertex AI pricing.
    price_fast_input_per_m: float = 0.25
    price_fast_output_per_m: float = 1.50
    price_quality_input_per_m: float = 0.30
    price_quality_output_per_m: float = 2.50
    price_grounded_prompt_usd: float = 0.035  # per grounded request; verify current Vertex pricing
    llm_daily_call_limit: int = 150
    llm_daily_budget_usd: float = 1.00
    llm_timeout_seconds: int = 60

    # --- Discovery ---
    http_user_agent: str = "AIJobHunter/0.1 (personal job search; contact: owner)"
    http_min_interval_seconds: float = 1.0

    # --- Notifications ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    notify_email_to: str = ""
    notify_email_from: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    public_base_url: str = "http://localhost:3000"
    # Cloud Scheduler service account allowed to call POST /api/internal/run-daily (Google-signed OIDC token).
    # Empty = endpoint disabled.
    cron_invoker_email: str = ""
    # Local use: when the app starts and the last successful daily run is older than this many hours, start one
    # in the background (0 = off).
    auto_refresh_hours: float = 0

    # --- Browser automation (local only) ---
    browser_automation_enabled: bool = False

    # --- Resume output ---
    resume_format: str = "latex"  # latex (Tectonic) | classic (ReportLab)

    # --- Uploads ---
    max_upload_mb: int = 5

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'jobhunter.db'}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def profile_path(self) -> Path:
        return self.data_dir / "profile.yaml"

    @property
    def search_config_path(self) -> Path:
        return self.data_dir / "search.yaml"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.uploads_dir, self.documents_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
