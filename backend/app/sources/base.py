import re
from dataclasses import dataclass, field
from datetime import datetime

from ..core.parsing import Salary


@dataclass
class RawPosting:
    """A posting as returned by a source, mapped to common fields.

    Fields a source cannot provide stay empty/None; they are never guessed.
    """
    provider: str
    board: str
    source_job_id: str
    company: str
    title: str
    source_url: str
    apply_url: str = ""
    location: str = ""
    locations: list[str] = field(default_factory=list)
    country_hint: str = ""
    workplace_hint: str = ""
    department: str = ""
    employment_type: str = ""
    description: str = ""
    posted_at: datetime | None = None
    posted_at_evidence: str = ""
    salary: Salary = field(default_factory=Salary)
    # True for web-search leads whose official page could not be read: stored as listing_status
    # "unverified" and never ranked until you import the description from the official page.
    is_lead: bool = False


@dataclass
class SourceResult:
    provider: str
    board: str
    company: str
    postings: list[RawPosting] = field(default_factory=list)
    complete: bool = True  # True only if the full board listing was fetched successfully
    error: str = ""
    fetched: int = 0
    skipped: int = 0
    seen_ids: set[str] = field(default_factory=set)


def _has_phrase(text: str, phrase: str) -> bool:
    p = phrase.lower().replace("-", " ").strip()
    return bool(p) and re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", text) is not None


def title_is_relevant(title: str, role_keywords: list[str], exclude: list[str]) -> bool:
    t = title.lower().replace("-", " ")
    if any(_has_phrase(t, x) for x in exclude):
        return False
    return any(_has_phrase(t, k) for k in role_keywords)
