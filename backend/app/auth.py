"""Single-user access control: password login → signed, HttpOnly session cookie."""
from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings, get_settings

COOKIE = "jh_session"
CSRF_HEADER = "x-requested-with"
_fallback_secret = secrets.token_urlsafe(32)
_attempts: dict[str, deque] = defaultdict(deque)


def _serializer(s: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(s.session_secret or _fallback_secret, salt="jh-session")


def check_rate_limit(ip: str, limit: int = 10, window: int = 900) -> None:
    q = _attempts[ip]
    now = time.monotonic()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many failed login attempts; try again later")


def login(response: Response, password: str, request: Request, s: Settings) -> None:
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)
    if not s.app_password:
        raise HTTPException(503, "APP_PASSWORD is not configured on the server")
    if not hmac.compare_digest(password.encode(), s.app_password.encode()):
        _attempts[ip].append(time.monotonic())
        raise HTTPException(401, "Invalid password")
    token = _serializer(s).dumps({"u": "owner", "n": secrets.token_hex(8)})
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=s.session_max_age_hours * 3600, path="/")


def logout(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def is_authenticated(request: Request, s: Settings) -> bool:
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    try:
        data = _serializer(s).loads(token, max_age=s.session_max_age_hours * 3600)
    except (BadSignature, SignatureExpired):
        return False
    return data.get("u") == "owner"


def require_auth(request: Request, s: Settings = Depends(get_settings)) -> None:
    if not is_authenticated(request, s):
        raise HTTPException(401, "Not authenticated")
    # CSRF defence in depth (cookie is SameSite=Strict): state-changing requests must carry a custom header,
    # which browsers do not allow cross-site forms to set.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "jobhunter":
        raise HTTPException(403, "Missing CSRF header")
