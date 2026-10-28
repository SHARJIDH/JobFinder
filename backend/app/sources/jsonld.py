"""schema.org JobPosting (JSON-LD) extraction from official job pages.

Many career sites embed structured JobPosting data for search engines. Reading it
from an official page (only when robots.txt allows) gives verified title,
description, posting date and — when published — salary.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..core.parsing import Salary, parse_salary_text
from ..core.text import html_to_text
from .base import RawPosting
from .http import PoliteClient, SourceBlocked

_LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        t = node.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield node
        for k in ("@graph", "mainEntity", "itemListElement"):
            if k in node:
                yield from _walk(node[k])


def extract_jobpostings(html: str) -> list[dict]:
    out = []
    for m in _LD.finditer(html or ""):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except ValueError:
            try:  # some sites emit control characters inside strings
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", raw))
            except ValueError:
                continue
        out.extend(_walk(data))
    return out


def _date(v) -> datetime | None:
    if not v:
        return None
    s = str(v).strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        if not m:
            return None
        d = datetime.fromisoformat(m.group(1))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _locations(jp: dict) -> tuple[list[str], bool]:
    locs = jp.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    out = []
    for loc in locs:
        addr = (loc or {}).get("address") if isinstance(loc, dict) else None
        if isinstance(addr, dict):
            country = addr.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), country]
            text = ", ".join(str(p) for p in parts if p)
            if text:
                out.append(text)
        elif isinstance(addr, str):
            out.append(addr)
    remote = str(jp.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    return out, remote


def _salary(jp: dict) -> Salary:
    bs = jp.get("baseSalary") or jp.get("estimatedSalary")
    if isinstance(bs, list):
        bs = bs[0] if bs else None
    if not isinstance(bs, dict):
        return Salary()
    val = bs.get("value")
    lo = hi = None
    unit = ""
    if isinstance(val, dict):
        lo, hi = val.get("minValue"), val.get("maxValue")
        if lo is None and hi is None and val.get("value") not in (None, ""):
            lo = hi = val.get("value")
        unit = str(val.get("unitText") or "")
    elif isinstance(val, (int, float, str)) and str(val).strip():
        lo = hi = val
    try:
        lo = float(lo) if lo not in (None, "") else None
        hi = float(hi) if hi not in (None, "") else None
    except (TypeError, ValueError):
        return Salary()
    if lo is None and hi is None:
        return Salary()
    period = {"YEAR": "year", "MONTH": "month", "HOUR": "hour"}.get(unit.upper(), "year")
    kind = "estimated" if "estimatedSalary" in jp and "baseSalary" not in jp else "base"
    return Salary(lo, hi, str(bs.get("currency") or ""), period, kind, "JSON-LD baseSalary", "")


def jobposting_to_raw(jp: dict, url: str, company: str, provider: str, board: str, now: datetime | None = None) -> RawPosting | None:
    """Map a JobPosting object to RawPosting. Returns None for expired postings."""
    now = now or datetime.now(timezone.utc)
    valid = _date(jp.get("validThrough"))
    if valid and valid < now:
        return None
    title = html_to_text(str(jp.get("title") or "")).strip()
    if not title:
        return None
    locs, remote = _locations(jp)
    desc = html_to_text(str(jp.get("description") or ""))
    posted = _date(jp.get("datePosted"))
    ident = jp.get("identifier")
    if isinstance(ident, dict):
        ident = ident.get("value")
    sal = _salary(jp)
    if sal.min is None:
        sal = parse_salary_text(desc)
    emp = jp.get("employmentType")
    return RawPosting(
        provider=provider, board=board, source_job_id=str(ident or urlsplit(url).path)[:200], company=company,
        title=title, source_url=url, apply_url=str(jp.get("url") or url) if str(jp.get("url") or "").startswith("https://") else url,
        location=locs[0] if locs else "", locations=locs[1:], workplace_hint="remote" if remote else "",
        employment_type=", ".join(emp) if isinstance(emp, list) else str(emp or ""),
        description=desc[:30000], posted_at=posted,
        posted_at_evidence="JSON-LD datePosted on official page" if posted else "", salary=sal,
    )


def fetch_jsonld_posting(http: PoliteClient, url: str, company: str, provider: str, board: str) -> RawPosting | None:
    """Fetch an official page (robots.txt respected) and return its JobPosting, if any."""
    r = http.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
    if r.status_code in (404, 410):
        return None
    r.raise_for_status()
    for jp in extract_jobpostings(r.text[:3_000_000]):
        rp = jobposting_to_raw(jp, str(r.url), company, provider, board)
        if rp:
            return rp
    return None


__all__ = ["extract_jobpostings", "jobposting_to_raw", "fetch_jsonld_posting", "SourceBlocked"]
