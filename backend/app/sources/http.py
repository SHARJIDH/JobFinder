"""Polite HTTP client: per-host rate limiting, robots.txt checks, bounded retries."""
import re
import threading
import time
from urllib.parse import urlsplit

import httpx

from ..config import get_settings


class SourceBlocked(Exception):
    """Raised when robots.txt or the server disallows access."""


class Robots:
    """RFC 9309 robots.txt evaluation: the most specific (longest) matching rule wins; Allow wins ties.
    (Python's urllib.robotparser uses first-match order, which wrongly blocks e.g. 'Disallow: /' + 'Allow: /api'.)"""

    def __init__(self, text: str, agent: str):
        self.rules: list[tuple[bool, str]] = []
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not last_was_agent and agents:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(val.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                if agents and val:
                    rules.append((key == "allow", val))
            else:
                last_was_agent = False
        if agents:
            groups.append((agents, rules))
        token = agent.split("/")[0].lower()
        specific = [r for a, r in groups if any(x != "*" and x in token for x in a)]
        chosen = specific or [r for a, r in groups if "*" in a]
        for r in chosen:
            self.rules.extend(r)

    @staticmethod
    def _match(pattern: str, path: str) -> int:
        anchored = pattern.endswith("$")
        rx = re.escape(pattern.rstrip("$")).replace(r"\*", ".*")
        return len(pattern) if re.match(rx + ("$" if anchored else ""), path) else -1

    def allowed(self, path: str) -> bool:
        best_len, best_allow = -1, True
        for allow, pat in self.rules:
            n = self._match(pat, path)
            if n > best_len or (n == best_len and allow):
                best_len, best_allow = n, allow
        return best_allow if best_len >= 0 else True


class PoliteClient:
    def __init__(self, client: httpx.Client | None = None, min_interval: float | None = None,
                 user_agent: str | None = None, check_robots: bool = True):
        s = get_settings()
        self.user_agent = user_agent or s.http_user_agent
        self.min_interval = s.http_min_interval_seconds if min_interval is None else min_interval
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )
        self.check_robots = check_robots
        self._last: dict[str, float] = {}
        self._robots: dict[str, Robots | None] = {}
        self._lock = threading.Lock()

    def _wait(self, host: str) -> None:
        with self._lock:
            last = self._last.get(host, 0.0)
            delta = time.monotonic() - last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last[host] = time.monotonic()

    def robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp: Robots | None
            try:
                r = self.client.get(base + "/robots.txt", headers={"Accept": "text/plain"})
                rp = None if r.status_code >= 400 else Robots(r.text, self.user_agent)  # 4xx -> no restrictions
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        return True if rp is None else rp.allowed(path)

    def request(self, method: str, url: str, *, respect_robots: bool = True, retries: int = 2, **kw) -> httpx.Response:
        if self.check_robots and respect_robots and not self.robots_allows(url):
            raise SourceBlocked(f"robots.txt disallows {url}")
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            self._wait(host)
            resp = self.client.request(method, url, **kw)
            if resp.status_code in (429, 502, 503, 504) and attempt < retries:
                retry_after = resp.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 2.0 * (attempt + 1)
                time.sleep(min(delay, 30))
                attempt += 1
                continue
            if resp.status_code in (401, 403):
                raise SourceBlocked(f"access denied ({resp.status_code}) for {url}; not bypassing")
            return resp

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)

    def close(self) -> None:
        self.client.close()
