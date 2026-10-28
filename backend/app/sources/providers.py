"""Job-source connectors for public, permitted endpoints of official career sites.

Greenhouse/Lever parsing is adapted from Insideofcub/ai-job-hunter (MIT),
extended with stable source ids, compensation fields and Ashby/Workday/
SmartRecruiters/Eightfold/Amazon/Atlassian/Oracle/Google/sitemap support.
Every request goes through PoliteClient (rate limits, RFC 9309 robots.txt, no access-control bypass).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

import httpx

from ..core.parsing import Salary, parse_salary_text
from ..core.text import html_to_text
from .base import RawPosting, SourceResult, title_is_relevant
from .http import PoliteClient, SourceBlocked

MAX_DESC = 30000


@dataclass
class FetchContext:
    role_keywords: list[str]
    exclude_keywords: list[str]
    home_country: str = "India"
    workday_max_details: int = 40
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    known_ids: set[str] = field(default_factory=set)  # ids already stored for the source being fetched

    def relevant(self, title: str) -> bool:
        return title_is_relevant(title, self.role_keywords, self.exclude_keywords)


def _iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


# ------------------------------------------------------------------ Greenhouse

def fetch_greenhouse(http: PoliteClient, board: str, company: str, ctx: FetchContext) -> SourceResult:
    res = SourceResult("greenhouse", board, company)
    url = f"https://boards-api.greenhouse.io/v1/boards/{quote(board, safe='')}/jobs"
    r = http.get(url, params={"content": "true"})
    if r.status_code == 404:
        res.complete, res.error = False, f"Greenhouse board '{board}' not found (404)"
        return res
    r.raise_for_status()
    jobs = (r.json() or {}).get("jobs", [])
    res.fetched = len(jobs)
    for j in jobs:
        title = str(j.get("title") or "").strip()
        jid = str(j.get("id") or "")
        link = str(j.get("absolute_url") or "")
        res.seen_ids.add(jid)
        if not title or not jid or not link.startswith("https://"):
            continue
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        loc = (j.get("location") or {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location") or "")
        offices = [o.get("location") or o.get("name") or "" for o in j.get("offices") or [] if isinstance(o, dict)]
        posted = _iso(j.get("first_published"))
        desc = html_to_text(j.get("content"))[:MAX_DESC]
        res.postings.append(RawPosting(
            provider="greenhouse", board=board, source_job_id=jid, company=company, title=title,
            source_url=link, apply_url=link, location=loc, locations=[x for x in offices if x],
            department=", ".join(d.get("name", "") for d in j.get("departments") or [] if isinstance(d, dict)),
            description=desc, posted_at=posted,
            posted_at_evidence="greenhouse first_published" if posted else "",
            salary=parse_salary_text(desc),
        ))
    return res


def verify_greenhouse(http: PoliteClient, board: str, job_id: str) -> bool | None:
    r = http.get(f"https://boards-api.greenhouse.io/v1/boards/{quote(board, safe='')}/jobs/{quote(job_id, safe='')}")
    if r.status_code == 404:
        return False
    return True if r.status_code == 200 else None


# ------------------------------------------------------------------ Lever

def fetch_lever(http: PoliteClient, board: str, company: str, ctx: FetchContext) -> SourceResult:
    res = SourceResult("lever", board, company)
    url = f"https://api.lever.co/v0/postings/{quote(board, safe='')}"
    postings: list[dict] = []
    skip, page = 0, 100
    while True:
        r = http.get(url, params={"mode": "json", "limit": page, "skip": skip})
        if r.status_code == 404:
            res.complete, res.error = False, f"Lever board '{board}' not found (404)"
            return res
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list):
            break
        postings.extend(batch)
        if len(batch) < page or skip > 5000:
            break
        skip += page
    res.fetched = len(postings)
    for p in postings:
        title = str(p.get("text") or "").strip()
        pid = str(p.get("id") or "")
        hosted = str(p.get("hostedUrl") or "")
        res.seen_ids.add(pid)
        if not title or not pid or not hosted.startswith("https://"):
            continue
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        cats = p.get("categories") or {}
        lists = "\n".join(
            f"{li.get('text', '')}:\n{html_to_text(li.get('content', ''))}" for li in p.get("lists") or [] if isinstance(li, dict)
        )
        desc = "\n\n".join(x for x in [str(p.get("descriptionPlain") or ""), lists, str(p.get("additionalPlain") or "")] if x)[:MAX_DESC]
        created = p.get("createdAt")
        posted = datetime.fromtimestamp(float(created) / 1000, tz=timezone.utc) if created else None
        sal = Salary()
        sr = p.get("salaryRange") or {}
        if isinstance(sr, dict) and (sr.get("min") or sr.get("max")):
            sal = Salary(sr.get("min"), sr.get("max"), str(sr.get("currency") or "").upper(),
                         {"per-year-salary": "year", "per-month-salary": "month", "per-hour-wage": "hour"}.get(sr.get("interval", ""), "year"),
                         "base", "lever salaryRange", str(p.get("salaryDescriptionPlain") or "")[:200])
        else:
            sal = parse_salary_text(desc)
        res.postings.append(RawPosting(
            provider="lever", board=board, source_job_id=pid, company=company, title=title,
            source_url=hosted, apply_url=str(p.get("applyUrl") or hosted),
            location=str(cats.get("location") or ""), locations=list(cats.get("allLocations") or []),
            country_hint=str(p.get("country") or ""), workplace_hint=str(p.get("workplaceType") or ""),
            department=str(cats.get("team") or cats.get("department") or ""),
            employment_type=str(cats.get("commitment") or ""), description=desc, posted_at=posted,
            posted_at_evidence="lever createdAt" if posted else "", salary=sal,
        ))
    return res


def verify_lever(http: PoliteClient, board: str, job_id: str) -> bool | None:
    r = http.get(f"https://api.lever.co/v0/postings/{quote(board, safe='')}/{quote(job_id, safe='')}", params={"mode": "json"})
    if r.status_code == 404:
        return False
    return True if r.status_code == 200 else None


# ------------------------------------------------------------------ Ashby

def _ashby_salary(comp: dict | None) -> Salary:
    if not isinstance(comp, dict):
        return Salary()
    for c in comp.get("summaryComponents") or []:
        if not isinstance(c, dict):
            continue
        ctype = str(c.get("compensationType") or "")
        if ctype.lower() not in ("salary", "base salary"):
            continue
        interval = str(c.get("interval") or "").lower()
        period = "year" if "year" in interval else "month" if "month" in interval else "hour" if "hour" in interval else "year"
        return Salary(c.get("minValue"), c.get("maxValue"), str(c.get("currencyCode") or ""), period, "base",
                      "ashby compensation", str(comp.get("compensationTierSummary") or "")[:200])
    summary = comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary")
    if summary:
        s = parse_salary_text(str(summary))
        if s.min is not None:
            s.source, s.kind = "ashby compensation summary", "total" if "equity" in str(summary).lower() else s.kind
            return s
    return Salary()


def fetch_ashby(http: PoliteClient, board: str, company: str, ctx: FetchContext) -> SourceResult:
    res = SourceResult("ashby", board, company)
    r = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{quote(board, safe='')}", params={"includeCompensation": "true"})
    if r.status_code == 404:
        res.complete, res.error = False, f"Ashby board '{board}' not found (404)"
        return res
    r.raise_for_status()
    jobs = (r.json() or {}).get("jobs", [])
    res.fetched = len(jobs)
    for j in jobs:
        if j.get("isListed") is False:
            continue
        title = str(j.get("title") or "").strip()
        jid = str(j.get("id") or "")
        url = str(j.get("jobUrl") or "")
        res.seen_ids.add(jid)
        if not title or not jid or not url.startswith("https://"):
            continue
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        addr = ((j.get("address") or {}).get("postalAddress") or {}) if isinstance(j.get("address"), dict) else {}
        secondary = [s.get("location", "") for s in j.get("secondaryLocations") or [] if isinstance(s, dict)]
        desc = (str(j.get("descriptionPlain") or "") or html_to_text(j.get("descriptionHtml")))[:MAX_DESC]
        posted = _iso(j.get("publishedAt"))
        sal = _ashby_salary(j.get("compensation"))
        if sal.min is None:
            sal = parse_salary_text(desc)
        res.postings.append(RawPosting(
            provider="ashby", board=board, source_job_id=jid, company=company, title=title,
            source_url=url, apply_url=str(j.get("applyUrl") or url),
            location=str(j.get("location") or ""), locations=[x for x in secondary if x],
            country_hint=str(addr.get("addressCountry") or ""),
            workplace_hint="remote" if j.get("isRemote") else str(j.get("workplaceType") or ""),
            department=str(j.get("department") or ""), employment_type=str(j.get("employmentType") or ""),
            description=desc, posted_at=posted, posted_at_evidence="ashby publishedAt" if posted else "", salary=sal,
        ))
    return res


# ------------------------------------------------------------------ SmartRecruiters (opt-in)

def fetch_smartrecruiters(http: PoliteClient, board: str, company: str, ctx: FetchContext) -> SourceResult:
    """Documented public Posting API. Its robots.txt disallows generic agents, so this
    connector only runs when the user explicitly enables the source."""
    res = SourceResult("smartrecruiters", board, company)
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(board, safe='')}/postings"
    country = "in" if ctx.home_country.lower() == "india" else ""
    items: list[dict] = []
    offset = 0
    while True:
        params = {"limit": 100, "offset": offset}
        if country:
            params["country"] = country
        r = http.get(base, params=params, respect_robots=False)
        if r.status_code == 404:
            res.complete, res.error = False, f"SmartRecruiters company '{board}' not found"
            return res
        r.raise_for_status()
        data = r.json() or {}
        content = data.get("content") or []
        items.extend(content)
        offset += len(content)
        if not content or offset >= int(data.get("totalFound") or 0) or offset > 2000:
            break
    res.fetched = len(items)
    details = 0
    for it in items:
        title = str(it.get("name") or "").strip()
        pid = str(it.get("id") or "")
        res.seen_ids.add(pid)
        if not title or not pid:
            continue
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        desc = ""
        if details < ctx.workday_max_details:
            d = http.get(f"{base}/{quote(pid, safe='')}", respect_robots=False)
            details += 1
            if d.status_code == 200:
                sections = ((d.json() or {}).get("jobAd") or {}).get("sections") or {}
                desc = "\n\n".join(
                    f"{(v or {}).get('title', '')}\n{html_to_text((v or {}).get('text', ''))}" for v in sections.values() if isinstance(v, dict)
                )[:MAX_DESC]
        loc = it.get("location") or {}
        posted = _iso(it.get("releasedDate"))
        url = f"https://jobs.smartrecruiters.com/{quote(board, safe='')}/{quote(pid, safe='')}"
        res.postings.append(RawPosting(
            provider="smartrecruiters", board=board, source_job_id=pid, company=company, title=title,
            source_url=url, apply_url=url,
            location=", ".join(x for x in [loc.get("city"), loc.get("region"), str(loc.get("country") or "").upper()] if x),
            country_hint=str(loc.get("country") or ""), workplace_hint="remote" if loc.get("remote") else "",
            department=str((it.get("department") or {}).get("label") or ""),
            employment_type=str((it.get("typeOfEmployment") or {}).get("label") or ""),
            description=desc, posted_at=posted, posted_at_evidence="smartrecruiters releasedDate" if posted else "",
            salary=parse_salary_text(desc),
        ))
    return res


# ------------------------------------------------------------------ Workday (public career-site JSON)

_WD_RELATIVE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.I)


def _workday_relative(posted_on: str, now: datetime) -> tuple[datetime | None, str]:
    m = _WD_RELATIVE.search(posted_on or "")
    if not m:
        return None, ""
    if "+" in (posted_on or ""):
        return None, f"workday '{posted_on}' (older than stated; exact date unknown)"
    days = 0 if m.group(1).lower() == "today" else 1 if m.group(1).lower() == "yesterday" else int(m.group(2))
    return now - timedelta(days=days), f"workday postedOn '{posted_on}' (day precision)"


def fetch_workday(http: PoliteClient, board: str, company: str, ctx: FetchContext, search_terms: list[str]) -> SourceResult:
    res = SourceResult("workday", board, company)
    try:
        host, tenant, site = board.split("|")
    except ValueError:
        res.complete, res.error = False, "workday board must be 'host|tenant|site'"
        return res
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    # Workday search returns a relevance-ranked subset, so a full listing is not
    # guaranteed -> never infer closures from absence (complete=False).
    res.complete = False
    seen: dict[str, dict] = {}
    for term in search_terms or ["software engineer"]:
        offset = 0
        while offset < 100:
            r = http.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                                "searchText": f"{term} {ctx.home_country}".strip()},
                          headers={"Content-Type": "application/json"})
            if r.status_code >= 400:
                res.error = f"Workday search returned HTTP {r.status_code}"
                break
            data = r.json() or {}
            postings = data.get("jobPostings") or []
            for p in postings:
                path = p.get("externalPath")
                if path and path not in seen:
                    seen[path] = p
            offset += 20
            if len(postings) < 20 or offset >= int(data.get("total") or 0):
                break
    res.fetched = len(seen)
    details = 0
    for path, p in seen.items():
        title = str(p.get("title") or "").strip()
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        loc_text = str(p.get("locationsText") or "")
        if ctx.home_country and ctx.home_country.lower() not in (loc_text + path).lower() and "location" not in loc_text.lower():
            res.skipped += 1
            continue
        if any(str(b) in ctx.known_ids for b in p.get("bulletFields") or []):
            continue
        if details >= ctx.workday_max_details:
            break
        d = http.get(f"{api}{path}")
        details += 1
        if d.status_code != 200:
            continue
        info = (d.json() or {}).get("jobPostingInfo") or {}
        if info.get("posted") is False:
            continue
        locs = [str(info.get("location") or "")] + [str(x) for x in info.get("additionalLocations") or []]
        start = info.get("startDate")
        posted = _iso(start) if start else None
        evidence = "workday startDate" if posted else ""
        if not posted:
            posted, evidence = _workday_relative(str(info.get("postedOn") or p.get("postedOn") or ""), ctx.now)
        desc = html_to_text(info.get("jobDescription"))[:MAX_DESC]
        ext = str(info.get("externalUrl") or f"https://{host}/{site}{path}")
        res.postings.append(RawPosting(
            provider="workday", board=board, source_job_id=str(info.get("jobReqId") or info.get("id") or path),
            company=company, title=str(info.get("title") or title), source_url=ext, apply_url=ext,
            location=locs[0], locations=[x for x in locs[1:] if x],
            country_hint=str((info.get("country") or {}).get("descriptor") or ""),
            employment_type=str(info.get("timeType") or ""), description=desc,
            posted_at=posted, posted_at_evidence=evidence, salary=parse_salary_text(desc),
        ))
    return res


# ------------------------------------------------------------------ Eightfold PCSX (Microsoft, Qualcomm, …)

def fetch_eightfold(http: PoliteClient, board: str, company: str, ctx: FetchContext, search_terms: list[str]) -> SourceResult:
    """Career sites on Eightfold expose /api/pcsx, which their robots.txt explicitly allows.
    board = "host|domain", e.g. "apply.careers.microsoft.com|microsoft.com"."""
    res = SourceResult("eightfold", board, company, complete=False)  # search results never prove closure
    try:
        host, domain = board.split("|")
    except ValueError:
        res.error = "eightfold board must be 'host|domain'"
        return res
    base = f"https://{host}/api/pcsx"
    seen: dict[str, dict] = {}
    for term in search_terms or ["software engineer"]:
        for start in range(0, 50, 10):
            r = http.get(f"{base}/search", params={"domain": domain, "query": term, "location": ctx.home_country,
                                                   "start": start, "sort_by": "timestamp"})
            if r.status_code >= 400:
                res.error = f"Eightfold search returned HTTP {r.status_code}"
                break
            positions = ((r.json() or {}).get("data") or {}).get("positions") or []
            for p in positions:
                seen.setdefault(str(p.get("id")), p)
            if len(positions) < 10:
                break
    res.fetched = len(seen)
    details = 0
    for pid, p in seen.items():
        title = str(p.get("name") or "").strip()
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        if str(p.get("displayJobId") or pid) in ctx.known_ids:
            continue
        if details >= ctx.workday_max_details:
            break
        d = http.get(f"{base}/position_details", params={"position_id": pid, "domain": domain, "hl": "en"})
        details += 1
        info = ((d.json() or {}).get("data") or {}) if d.status_code == 200 else {}
        if not info:
            continue
        ts = info.get("postedTs") or p.get("postedTs")
        posted = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
        locs = [str(x) for x in (info.get("locations") or p.get("locations") or [])]
        url = str(info.get("publicUrl") or f"https://{host}{info.get('positionUrl') or p.get('positionUrl') or ''}")
        desc = html_to_text(info.get("jobDescription"))[:MAX_DESC]
        res.postings.append(RawPosting(
            provider="eightfold", board=board, source_job_id=str(info.get("displayJobId") or pid), company=company,
            title=str(info.get("name") or title), source_url=url, apply_url=url, location=locs[0] if locs else "",
            locations=locs[1:], workplace_hint=str(info.get("workLocationOption") or ""),
            department=str(info.get("department") or ""), description=desc, posted_at=posted,
            posted_at_evidence="eightfold postedTs" if posted else "", salary=parse_salary_text(desc),
        ))
    return res


# ------------------------------------------------------------------ Amazon Jobs

def _amazon_date(v: str) -> datetime | None:
    try:
        return datetime.strptime(v.strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def fetch_amazon(http: PoliteClient, board: str, company: str, ctx: FetchContext, search_terms: list[str]) -> SourceResult:
    """amazon.jobs search JSON (robots.txt only disallows /internal). board = ISO-3 country code, e.g. IND."""
    res = SourceResult("amazon", board, company, complete=False)
    seen: dict[str, dict] = {}
    for term in search_terms or ["software development engineer"]:
        for offset in range(0, 300, 100):
            r = http.get("https://www.amazon.jobs/en/search.json", params={
                "base_query": term, "normalized_country_code[]": board or "IND", "result_limit": 100,
                "offset": offset, "sort": "recent"})
            if r.status_code >= 400:
                res.error = f"amazon.jobs returned HTTP {r.status_code}"
                break
            data = r.json() or {}
            jobs = data.get("jobs") or []
            for j in jobs:
                seen.setdefault(str(j.get("id_icims") or j.get("id")), j)
            if len(jobs) < 100:
                break
    res.fetched = len(seen)
    for jid, j in seen.items():
        title = str(j.get("title") or "").strip()
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        desc = "\n\n".join(x for x in [
            html_to_text(j.get("description")),
            "Basic qualifications:\n" + html_to_text(j.get("basic_qualifications")) if j.get("basic_qualifications") else "",
            "Preferred qualifications:\n" + html_to_text(j.get("preferred_qualifications")) if j.get("preferred_qualifications") else "",
        ] if x)[:MAX_DESC]
        posted = _amazon_date(str(j.get("posted_date") or ""))
        url = "https://www.amazon.jobs" + str(j.get("job_path") or f"/en/jobs/{jid}")
        res.postings.append(RawPosting(
            provider="amazon", board=board, source_job_id=jid, company=company, title=title, source_url=url,
            apply_url=str(j.get("url_next_step") or url), location=str(j.get("normalized_location") or j.get("location") or ""),
            country_hint=str(j.get("country_code") or ""), department=str(j.get("job_category") or ""),
            employment_type=str(j.get("job_schedule_type") or ""), description=desc, posted_at=posted,
            posted_at_evidence="amazon.jobs posted_date (day precision)" if posted else "", salary=parse_salary_text(desc),
        ))
    return res


# ------------------------------------------------------------------ Atlassian careers listing

def fetch_atlassian(http: PoliteClient, board: str, company: str, ctx: FetchContext) -> SourceResult:
    """Atlassian's public careers listings JSON (the full list, so closures can be inferred)."""
    res = SourceResult("atlassian", board, company)
    r = http.get("https://www.atlassian.com/endpoint/careers/listings")
    r.raise_for_status()
    jobs = r.json() or []
    res.fetched = len(jobs)
    for j in jobs:
        jid = str(j.get("id") or "")
        title = str(j.get("title") or "").strip()
        if not jid or not title:
            continue
        res.seen_ids.add(jid)
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        locs = [str(x) for x in j.get("locations") or []]
        desc = "\n\n".join(html_to_text(j.get(k)) for k in ("overview", "responsibilities", "qualifications") if j.get(k))[:MAX_DESC]
        portal = j.get("portalJobPost") or {}
        url = f"https://www.atlassian.com/company/careers/details/{jid}"
        res.postings.append(RawPosting(
            provider="atlassian", board=board, source_job_id=jid, company=company, title=title, source_url=url,
            apply_url=str(j.get("applyUrl") or portal.get("portalUrl") or url), location=locs[0] if locs else "",
            locations=locs[1:], department=str(j.get("category") or ""), employment_type=str(j.get("type") or ""),
            # Only an "updatedDate" is published, which is not proof of the original posting date.
            description=desc, posted_at=None, posted_at_evidence="", salary=parse_salary_text(desc),
        ))
    return res


# ------------------------------------------------------------------ Oracle Recruiting Cloud (HCM REST)

def fetch_oracle(http: PoliteClient, board: str, company: str, ctx: FetchContext, search_terms: list[str]) -> SourceResult:
    """Oracle Recruiting Cloud candidate-experience REST API. board = "host|siteNumber|locationId"."""
    res = SourceResult("oracle", board, company, complete=False)
    try:
        host, site, location_id = board.split("|")
    except ValueError:
        res.error = "oracle board must be 'host|siteNumber|locationId'"
        return res
    api = f"https://{host}/hcmRestApi/resources/latest"
    seen: dict[str, dict] = {}
    for term in search_terms or ["software engineer"]:
        for offset in range(0, 100, 25):
            finder = (f'findReqs;siteNumber={site},limit=25,offset={offset},keyword="{term}",'
                      f"locationId={location_id},sortBy=POSTING_DATES_DESC")
            r = http.get(f"{api}/recruitingCEJobRequisitions", params={"onlyData": "true", "finder": finder,
                                                                       "expand": "requisitionList.secondaryLocations"})
            if r.status_code >= 400:
                res.error = f"Oracle REST returned HTTP {r.status_code}"
                break
            items = (r.json() or {}).get("items") or []
            reqs = (items[0].get("requisitionList") if items else None) or []
            for q in reqs:
                seen.setdefault(str(q.get("Id")), q)
            if len(reqs) < 25:
                break
    res.fetched = len(seen)
    details = 0
    for rid, q in seen.items():
        title = str(q.get("Title") or "").strip()
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        if rid in ctx.known_ids:
            continue
        if details >= ctx.workday_max_details:
            break
        d = http.get(f"{api}/recruitingCEJobRequisitionDetails", params={
            "onlyData": "true", "expand": "all", "finder": f'ById;Id="{rid}",siteNumber={site}'})
        details += 1
        items = (d.json() or {}).get("items") or [] if d.status_code == 200 else []
        info = items[0] if items else {}
        desc = "\n\n".join(html_to_text(info.get(k)) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                                                 "ExternalQualificationsStr") if info.get(k))[:MAX_DESC]
        if not desc:
            desc = str(q.get("ShortDescriptionStr") or "")
        posted = _iso(q.get("PostedDate"))
        url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}"
        secondary = [str(s.get("Name") or "") for s in q.get("secondaryLocations") or [] if isinstance(s, dict)]
        res.postings.append(RawPosting(
            provider="oracle", board=board, source_job_id=rid, company=company, title=title, source_url=url, apply_url=url,
            location=str(q.get("PrimaryLocation") or ""), locations=[x for x in secondary if x],
            country_hint=str(q.get("PrimaryLocationCountry") or ""), workplace_hint=str(q.get("WorkplaceType") or ""),
            description=desc, posted_at=posted, posted_at_evidence="oracle PostedDate" if posted else "",
            salary=parse_salary_text(desc),
        ))
    return res


# ------------------------------------------------------------------ Sitemap + JSON-LD (e.g. Intuit)

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def fetch_sitemap_jsonld(http: PoliteClient, board: str, company: str, ctx: FetchContext, search_terms: list[str]) -> SourceResult:
    """Read the company's sitemap, keep job URLs whose slug matches a home-country city and a role keyword,
    then read each page's schema.org JobPosting. board = "sitemap_url|url_regex"."""
    from .jsonld import fetch_jsonld_posting

    res = SourceResult("sitemap", board, company)
    sitemap, _, pattern = board.partition("|")
    rx = re.compile(pattern or r"/job/", re.I)
    r = http.get(sitemap, headers={"Accept": "application/xml,text/xml"})
    r.raise_for_status()
    urls = [u for u in _LOC.findall(r.text) if rx.search(u)]
    res.fetched = len(urls)
    for u in urls:
        res.seen_ids.add(u.rstrip("/").rsplit("/", 1)[-1])
    details = 0
    for u in urls:
        path_text = urlsplit(u).path.replace("-", " ").replace("/", " ")
        if not ctx.relevant(path_text):
            res.skipped += 1
            continue
        if u.rstrip("/").rsplit("/", 1)[-1] in ctx.known_ids:
            continue  # already stored; still counted as seen
        if details >= ctx.workday_max_details:
            break
        details += 1
        try:
            rp = fetch_jsonld_posting(http, u, company, "sitemap", board)
        except (SourceBlocked, httpx.HTTPError):
            continue
        if rp:
            rp.source_job_id = u.rstrip("/").rsplit("/", 1)[-1]
            res.postings.append(rp)
    # The sitemap lists every open job, so absence from it is evidence of closure (complete=True).
    return res


# ------------------------------------------------------------------ Google Careers (first results page only)

_GOOGLE_DS1 = re.compile(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel", re.S)


def _g(lst, i, default=None):
    return lst[i] if isinstance(lst, list) and len(lst) > i and lst[i] is not None else default


def fetch_google(http: PoliteClient, board: str, company: str, ctx: FetchContext, search_terms: list[str]) -> SourceResult:
    """Google Careers results pages, newest first. Google's robots.txt allows the first results page for
    generic agents but disallows pagination (?page=), so only page 1 of each query is read."""
    res = SourceResult("google", board, company, complete=False)
    seen: dict[str, list] = {}
    for term in search_terms or ["software engineer"]:
        r = http.get("https://www.google.com/about/careers/applications/jobs/results/",
                     params={"location": board or ctx.home_country, "q": term, "sort_by": "date"},
                     headers={"Accept": "text/html"})
        if r.status_code >= 400:
            res.error = f"Google Careers returned HTTP {r.status_code}"
            continue
        m = _GOOGLE_DS1.search(r.text)
        try:
            jobs = json.loads(m.group(1))[0] if m else None
        except (ValueError, IndexError, TypeError):
            jobs = None
        if not isinstance(jobs, list):
            res.error = "Google Careers page format not recognised (web search fallback still applies)"
            continue
        for j in jobs:
            if isinstance(j, list) and _g(j, 0) and _g(j, 1):
                seen.setdefault(str(j[0]), j)
    res.fetched = len(seen)
    for jid, j in seen.items():
        title = str(j[1]).strip()
        if not ctx.relevant(title):
            res.skipped += 1
            continue
        locs = [str(_g(x, 0, "")) for x in (_g(j, 9, []) or []) if isinstance(x, list)]
        parts = [("About the job", _g(_g(j, 10, []), 1, "")), ("Responsibilities", _g(_g(j, 3, []), 1, "")),
                 ("Qualifications", _g(_g(j, 4, []), 1, "")), ("", _g(_g(j, 19, []), 1, ""))]
        desc = "\n\n".join(f"{h}:\n{html_to_text(t)}" if h else html_to_text(t) for h, t in parts if t)[:MAX_DESC]
        ts = _g(_g(j, 12, []), 0)
        posted = datetime.fromtimestamp(int(ts), tz=timezone.utc) if isinstance(ts, (int, float)) else None
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        url = f"https://www.google.com/about/careers/applications/jobs/results/{jid}-{slug}"
        res.postings.append(RawPosting(
            provider="google", board=board, source_job_id=jid, company=company, title=title, source_url=url, apply_url=url,
            location=locs[0] if locs else "", locations=locs[1:], description=desc, posted_at=posted,
            posted_at_evidence="Google Careers listing timestamp" if posted else "", salary=parse_salary_text(desc),
        ))
    return res


# ------------------------------------------------------------------ dispatch

def fetch_source(http: PoliteClient, provider: str, board: str, company: str, ctx: FetchContext,
                 search_terms: list[str] | None = None) -> SourceResult:
    try:
        if provider == "greenhouse":
            return fetch_greenhouse(http, board, company, ctx)
        if provider == "lever":
            return fetch_lever(http, board, company, ctx)
        if provider == "ashby":
            return fetch_ashby(http, board, company, ctx)
        if provider == "smartrecruiters":
            return fetch_smartrecruiters(http, board, company, ctx)
        if provider == "workday":
            return fetch_workday(http, board, company, ctx, search_terms or [])
        if provider == "eightfold":
            return fetch_eightfold(http, board, company, ctx, search_terms or [])
        if provider == "amazon":
            return fetch_amazon(http, board, company, ctx, search_terms or [])
        if provider == "atlassian":
            return fetch_atlassian(http, board, company, ctx)
        if provider == "oracle":
            return fetch_oracle(http, board, company, ctx, search_terms or [])
        if provider == "sitemap":
            return fetch_sitemap_jsonld(http, board, company, ctx, search_terms or [])
        if provider == "google":
            return fetch_google(http, board, company, ctx, search_terms or [])
        return SourceResult(provider, board, company, complete=False, error=f"unknown provider {provider}")
    except SourceBlocked as e:
        return SourceResult(provider, board, company, complete=False, error=f"blocked: {e}")
    except (httpx.HTTPError, ValueError) as e:
        return SourceResult(provider, board, company, complete=False, error=f"{type(e).__name__}: {e}"[:500])


def verify_posting(http: PoliteClient, provider: str, board: str, job_id: str, url: str) -> bool | None:
    """True=open, False=closed, None=unknown. Never bypasses access controls."""
    try:
        if provider == "greenhouse":
            return verify_greenhouse(http, board, job_id)
        if provider == "lever":
            return verify_lever(http, board, job_id)
        if provider == "ashby":
            r = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{quote(board, safe='')}")
            if r.status_code != 200:
                return None
            return any(str(j.get("id")) == job_id for j in (r.json() or {}).get("jobs", []))
        if provider == "workday" and url:
            host, tenant, site = board.split("|")
            path = url.split(f"/{site}", 1)[-1]
            r = http.get(f"https://{host}/wday/cxs/{tenant}/{site}{path}")
            if r.status_code == 404:
                return False
            if r.status_code == 200:
                return ((r.json() or {}).get("jobPostingInfo") or {}).get("posted", True) is not False
            return None
        if provider == "smartrecruiters":
            r = http.get(f"https://api.smartrecruiters.com/v1/companies/{quote(board, safe='')}/postings/{quote(job_id, safe='')}", respect_robots=False)
            return False if r.status_code == 404 else True if r.status_code == 200 else None
        if provider == "eightfold" and url:
            host, domain = board.split("|")
            pid = url.rstrip("/").rsplit("/", 1)[-1]
            r = http.get(f"https://{host}/api/pcsx/position_details", params={"position_id": pid, "domain": domain})
            if r.status_code == 404:
                return False
            return bool((r.json() or {}).get("data")) if r.status_code == 200 else None
        if provider == "atlassian":
            r = http.get("https://www.atlassian.com/endpoint/careers/listings")
            return any(str(j.get("id")) == job_id for j in r.json() or []) if r.status_code == 200 else None
        if provider == "oracle":
            host, site, _ = board.split("|")
            r = http.get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                         params={"onlyData": "true", "finder": f'ById;Id="{job_id}",siteNumber={site}'})
            return bool((r.json() or {}).get("items")) if r.status_code == 200 else None
        if provider in ("amazon", "google", "sitemap", "web") and url:
            from .jsonld import extract_jobpostings, jobposting_to_raw

            r = http.get(url, headers={"Accept": "text/html"})
            if r.status_code in (404, 410):
                return False
            if r.status_code != 200:
                return None
            if provider in ("amazon", "google"):
                return True
            jps = extract_jobpostings(r.text)
            if jps and all(jobposting_to_raw(jp, url, "", provider, board) is None for jp in jps):
                return False  # validThrough has passed
            return True if jps else None
    except (SourceBlocked, httpx.HTTPError, ValueError):
        return None
    return None
