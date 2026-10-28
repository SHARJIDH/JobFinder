"""Web-search discovery: Gemini with Grounding with Google Search, restricted to official career domains.

Guarantees:
  * URLs typed by the model are never used. Only grounding *sources* — pages Google Search actually
    returned — are considered, and only those on the company's official domains.
  * Titles come from Google's grounding supports (the exact output line backed by that source) or,
    better, from the official page's schema.org JobPosting when robots.txt and the site allow reading it.
  * Pages we cannot read (robots.txt / 403 bot protection — never bypassed) become *unverified leads*:
    stored with listing_status="unverified" and never ranked until you paste the official description.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from ..core.text import canonical_url, sha256_text
from ..core.yaml_config import Company, SearchConfig
from ..llm.service import LLMService
from ..sources.base import RawPosting, SourceResult
from ..sources.http import PoliteClient, SourceBlocked
from ..sources.jsonld import fetch_jsonld_posting
from ..sources.providers import FetchContext

REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)


@dataclass
class SearchBudget:
    queries_left: int
    fetches_left: int
    log: list[str] = field(default_factory=list)


def companies_to_search(cfg: SearchConfig) -> list[Company]:
    out = []
    for c in cfg.companies:
        if not c.domains():
            continue
        has_feed = any(s.enabled for s in c.sources)
        if c.web_search is True or (c.web_search is None and (cfg.web_search.scope == "all" or not has_feed)):
            out.append(c)
    return sorted(out, key=lambda c: c.priority)


def build_queries(company: Company, cfg: SearchConfig) -> list[str]:
    cities = cfg.locations.preferred_cities[:2] or [cfg.locations.home_country]
    host = company.domains()[0]
    qs = [f"{company.name} careers software engineer {city} {host}" for city in cities]
    qs.append(f"{company.name} careers backend engineer {cfg.locations.home_country}")
    qs.append(f"{company.name} careers machine learning AI engineer {cfg.locations.home_country}")
    return qs


def build_prompt(company: Company, cfg: SearchConfig) -> str:
    queries = "\n".join(f"- {q}" for q in build_queries(company, cfg))
    return (
        f"Run these Google searches:\n{queries}\n\n"
        f"Then list every individual {company.name} job posting you found that is located in "
        f"{cfg.locations.home_country} (or remote within it). Output one line per posting, exactly in the form\n"
        "Title | Location\n"
        "with no URLs, numbering or other text. Output nothing if you found none."
    )


def resolve(http: PoliteClient, uri: str) -> str:
    """Grounding sources are Google redirect links; resolve to the real URL without fetching the target."""
    host = urlsplit(uri).hostname or ""
    if not host.endswith(REDIRECT_HOSTS):
        return uri
    try:
        r = http.client.get(uri, follow_redirects=False, timeout=15)
    except httpx.HTTPError:
        return ""
    return r.headers.get("location", "") if r.status_code in (301, 302, 303, 307, 308) else ""


def _official(url: str, domains: list[str]) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    # must be a specific posting (an id in the path), not a search or landing page
    specific = bool(re.search(r"\d{4,}|/R-\d+", parts.path))
    return url.startswith("https://") and specific and any(host == d or host.endswith("." + d) for d in domains)


def _lines(text: str) -> list[tuple[str, str]]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if "|" not in line or "http" in line:
            continue
        title, _, loc = line.partition("|")
        if 3 <= len(title.strip()) <= 200:
            out.append((title.strip().strip("*"), loc.strip().strip("*")))
    return out


def search_company(http: PoliteClient, llm: LLMService, company: Company, cfg: SearchConfig, ctx: FetchContext,
                   budget: SearchBudget) -> SourceResult:
    res = SourceResult("websearch", company.name.lower(), company.name, complete=False)
    if budget.queries_left <= 0:
        res.error = "web-search query budget for this run exhausted"
        return res
    budget.queries_left -= 1
    _text, sources, supports, _ = llm.grounded_search(task="web_search", prompt=build_prompt(company, cfg))
    domains = company.domains()

    # source index -> official URL (aggregators and other non-official hosts are ignored)
    official: dict[int, str] = {}
    for i, s in enumerate(sources):
        real = resolve(http, s.get("uri", "")) if s.get("uri") else ""
        if real and _official(real, domains):
            official[i] = real
        elif real:
            budget.log.append(f"{company.name}: ignored non-official source {urlsplit(real).hostname}")
    # official URL -> (title, location) from the output line Google attributes to that source
    labels: dict[str, tuple[str, str]] = {}
    for sup in supports:
        lines = _lines(sup.get("text", ""))
        if not lines:
            continue
        for idx in sup.get("chunks", []):
            if idx in official and canonical_url(official[idx]) not in labels:
                labels[canonical_url(official[idx])] = lines[0]
    res.fetched = len(official)

    seen: set[str] = set()
    for url in official.values():
        key = canonical_url(url)
        if key in seen:
            continue
        seen.add(key)
        title, location = labels.get(key, ("", ""))
        if title and not ctx.relevant(title):
            res.skipped += 1
            continue
        posting: RawPosting | None = None
        if budget.fetches_left > 0:
            budget.fetches_left -= 1
            try:
                posting = fetch_jsonld_posting(http, url, company.name, "web", company.name.lower())
            except SourceBlocked as e:
                budget.log.append(f"{company.name}: official page not readable ({str(e)[:80]}); stored as lead")
            except httpx.HTTPError as e:
                budget.log.append(f"{company.name}: could not read {url[:100]}: {type(e).__name__}")
        if posting is not None:
            if not ctx.relevant(posting.title):
                res.skipped += 1
                continue
            posting.source_job_id = sha256_text(key)[:24]
            res.postings.append(posting)
        elif title:
            res.postings.append(RawPosting(
                provider="websearch", board=company.name.lower(), source_job_id=sha256_text(key)[:24],
                company=company.name, title=title[:300], source_url=url, apply_url=url, location=location[:300],
                description="", is_lead=True,
            ))
        else:
            budget.log.append(f"{company.name}: official result without an attributable title skipped: {url[:100]}")
    return res
