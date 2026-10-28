from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import applications, jobs, profile, system
from .config import REPO_DIR, get_settings
from .migrations import upgrade_db

log = logging.getLogger("jobhunter")


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if s.app_env != "test":
        upgrade_db()
        if not s.app_password:
            log.warning("APP_PASSWORD is not set: the dashboard cannot be unlocked until you set it.")
        if s.app_env == "production" and (not s.session_secret or not s.cookie_secure):
            raise RuntimeError("Production requires SESSION_SECRET and COOKIE_SECURE=true")
        if system.maybe_auto_refresh(s.auto_refresh_hours):
            log.info("Auto-refresh: daily run started in the background")
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="AI Job Hunter", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs" if s.app_env != "production" else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if s.app_env != "production" else None)
    if s.cors_origin_list and s.app_env != "production":
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")  # the LaTeX editor previews PDFs in a same-origin frame
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'self' blob:; frame-ancestors 'self'; "
            "base-uri 'self'; form-action 'self' https://www.overleaf.com",
        )
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    for r in (system.public, system.router, jobs.router, applications.router, profile.router):
        app.include_router(r)

    static_dir = Path(REPO_DIR / "frontend" / "out")
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


app = create_app()
