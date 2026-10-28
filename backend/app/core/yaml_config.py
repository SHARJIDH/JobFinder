"""Editable YAML configuration: candidate profile and job-search settings.

Both files live in DATA_DIR (git-ignored). On first run they are seeded from
the committed templates in /config.
"""
import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ..config import REPO_DIR, get_settings
from .text import stable_json_hash

TEMPLATE_DIR = REPO_DIR / "config"


# ---------------------------------------------------------------- profile

class Bullet(BaseModel):
    id: str = ""
    text: str


def _coerce_bullets(v):
    out = []
    for item in v or []:
        out.append({"text": item} if isinstance(item, str) else item)
    return out


class Contact(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""


class Education(BaseModel):
    id: str = ""
    degree: str
    institution: str
    start: str = ""
    end: str = ""
    grade: str = ""
    details: list[str] = []


class Experience(BaseModel):
    id: str = ""
    title: str
    company: str
    location: str = ""
    start: str = ""  # YYYY-MM
    end: str = ""  # YYYY-MM or "" when current
    technologies: list[str] = []
    bullets: list[Bullet] = []
    source: str = ""  # where this came from, e.g. "resume: AI resume" or "note: Hackathon"

    @field_validator("bullets", mode="before")
    @classmethod
    def _coerce(cls, v):
        return _coerce_bullets(v)


class Project(BaseModel):
    id: str = ""
    name: str
    url: str = ""
    technologies: list[str] = []
    bullets: list[Bullet] = []
    source: str = ""

    @field_validator("bullets", mode="before")
    @classmethod
    def _coerce(cls, v):
        return _coerce_bullets(v)


class Skill(BaseModel):
    name: str
    category: str = ""
    proficiency: Literal["beginner", "intermediate", "advanced", "expert", ""] = ""
    verified: bool = False  # set true once you confirm it against your resume


class Certification(BaseModel):
    name: str
    issuer: str = ""
    date: str = ""
    url: str = ""


class ContextNote(BaseModel):
    """Free-text context written by the candidate (projects, achievements, tools, impact…).
    Treated as candidate-provided facts: used for matching, tailoring and profile building."""
    id: str = ""
    title: str = ""
    text: str


class ApplicationFacts(BaseModel):
    """Facts used verbatim in application answers. Leave blank to be asked."""
    work_authorization_india: str = ""
    requires_visa_sponsorship: str = ""
    notice_period: str = ""
    current_ctc: str = ""
    expected_ctc: str = ""
    willing_to_relocate: str = ""
    earliest_start_date: str = ""


class Profile(BaseModel):
    contact: Contact = Contact()
    headline: str = ""
    summary: str = ""
    interests: list[str] = []
    education: list[Education] = []
    experience: list[Experience] = []
    projects: list[Project] = []
    skills: list[Skill] = []
    certifications: list[Certification] = []
    achievements: list[Bullet] = []
    context_notes: list[ContextNote] = []
    application_facts: ApplicationFacts = ApplicationFacts()
    extra_answers: dict[str, str] = {}

    @field_validator("achievements", mode="before")
    @classmethod
    def _coerce(cls, v):
        return _coerce_bullets(v)

    @model_validator(mode="after")
    def _assign_ids(self):
        def slug(s: str) -> str:
            return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30] or "item"

        seen: set[str] = set()

        def unique(base: str) -> str:
            cand, i = base, 2
            while cand in seen:
                cand, i = f"{base}-{i}", i + 1
            seen.add(cand)
            return cand

        for e in self.education:
            e.id = unique(e.id or "edu-" + slug(e.institution))
        for x in self.experience:
            x.id = unique(x.id or "exp-" + slug(x.company))
            for i, b in enumerate(x.bullets, 1):
                b.id = unique(b.id or f"{x.id}-b{i}")
        for p in self.projects:
            p.id = unique(p.id or "proj-" + slug(p.name))
            for i, b in enumerate(p.bullets, 1):
                b.id = unique(b.id or f"{p.id}-b{i}")
        for i, b in enumerate(self.achievements, 1):
            b.id = unique(b.id or f"ach-b{i}")
        for i, n in enumerate(self.context_notes, 1):
            n.id = unique(n.id or f"note-{i}")
        return self

    def years_of_experience(self, today: date | None = None) -> float:
        """Professional experience computed from dated roles (no overlap handling needed for MVP)."""
        today = today or date.today()
        months = 0
        for x in self.experience:
            start = _parse_ym(x.start)
            if not start:
                continue
            end = _parse_ym(x.end) or today
            months += max(0, (end.year - start.year) * 12 + end.month - start.month)
        return round(months / 12, 1)

    def all_bullets(self) -> dict[str, str]:
        out = {}
        for x in self.experience:
            out.update({b.id: b.text for b in x.bullets})
        for p in self.projects:
            out.update({b.id: b.text for b in p.bullets})
        out.update({b.id: b.text for b in self.achievements})
        return out

    def notes_text(self) -> str:
        return "\n\n".join(f"{n.title}\n{n.text}".strip() for n in self.context_notes)

    def fingerprint(self) -> str:
        return stable_json_hash(self.model_dump())


def _parse_ym(v: str) -> date | None:
    m = re.match(r"^(\d{4})-(\d{1,2})", v or "")
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), 1)


# ---------------------------------------------------------------- search settings

class CompanySource(BaseModel):
    provider: Literal["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "eightfold", "amazon",
                      "atlassian", "oracle", "sitemap", "google"]
    board: str  # provider-specific identifier, see config/search.example.yaml
    enabled: bool = True
    search_terms: list[str] = []  # search-based providers (workday, eightfold, amazon, oracle)
    notes: str = ""


class Company(BaseModel):
    name: str
    priority: int = Field(2, ge=1, le=3)  # 1 = strongest preference
    careers_url: str = ""
    official_domains: list[str] = []  # hosts that count as "official" for web-search results
    web_search: bool | None = None  # None = automatic (only when no feed is enabled)
    sources: list[CompanySource] = []
    notes: str = ""

    def domains(self) -> list[str]:
        if self.official_domains:
            return [d.lower().strip() for d in self.official_domains if d.strip()]
        from urllib.parse import urlsplit

        host = urlsplit(self.careers_url).hostname or ""
        return [host.lower()] if host else []


class RoleFamily(BaseModel):
    name: str
    keywords: list[str]
    priority: Literal["primary", "secondary"] = "primary"


class LocationPrefs(BaseModel):
    preferred_cities: list[str] = ["Hyderabad", "Bengaluru", "Pune", "Chennai", "Gurugram", "Noida"]
    home_country: str = "India"
    allow_other_home_country_cities: bool = True
    allow_remote: bool = True
    remote_regions_eligible: list[str] = ["India", "APAC", "Asia", "Worldwide", "Anywhere", "Global"]
    international_eligible_countries: list[str] = []


class CompensationPrefs(BaseModel):
    target_annual_lpa: float = 30.0
    fx_to_inr: dict[str, float] = {"USD": 84.0, "EUR": 91.0, "GBP": 107.0, "SGD": 63.0, "CAD": 61.0, "AUD": 55.0}


class FreshnessPrefs(BaseModel):
    preferred_hours: int = 72
    max_age_days: int = 60


class Weights(BaseModel):
    skills: float = 0.30
    role_fit: float = 0.20
    experience: float = 0.15
    compensation: float = 0.08
    company: float = 0.07
    location: float = 0.12
    freshness: float = 0.08
    required_skill_share: float = Field(0.75, ge=0, le=1)
    ai_weight: float = Field(0.35, ge=0, le=1)


class Tiers(BaseModel):
    a: float = 78
    b: float = 65
    c: float = 50


class ExperiencePrefs(BaseModel):
    hard_exclude_years_gap: float = 4.0  # exclude when min-required minus my years >= this


class Limits(BaseModel):
    max_ai_scores_per_run: int = 25
    ai_score_min_deterministic: float = 55
    auto_tailor_per_run: int = 3
    auto_tailor_min_tier: Literal["A", "B"] = "A"
    notify_max_jobs: int = 10
    notify_min_tier: Literal["A", "B", "C"] = "B"
    workday_max_details_per_company: int = 40


class WebSearchPrefs(BaseModel):
    """Gemini + Grounding with Google Search, restricted to each company's official domains."""
    enabled: bool = True
    scope: Literal["no_feed", "all"] = "no_feed"  # which companies to search
    max_queries_per_run: int = 12
    max_page_fetches_per_run: int = 30


class SearchConfig(BaseModel):
    companies: list[Company] = []
    roles: list[RoleFamily] = []
    exclude_title_keywords: list[str] = ["director", "vice president", "head of", "intern", "internship"]
    locations: LocationPrefs = LocationPrefs()
    compensation: CompensationPrefs = CompensationPrefs()
    freshness: FreshnessPrefs = FreshnessPrefs()
    weights: Weights = Weights()
    tiers: Tiers = Tiers()
    experience: ExperiencePrefs = ExperiencePrefs()
    limits: Limits = Limits()
    web_search: WebSearchPrefs = WebSearchPrefs()

    def fingerprint(self) -> str:
        return stable_json_hash(self.model_dump())

    def company(self, name: str) -> Company | None:
        n = name.lower().strip()
        for c in self.companies:
            if c.name.lower() == n:
                return c
        return None


# ---------------------------------------------------------------- load / save

def _ensure_seeded(path: Path, template_name: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((TEMPLATE_DIR / template_name).read_text(encoding="utf-8"), encoding="utf-8")


def load_profile(path: Path | None = None) -> Profile:
    path = path or get_settings().profile_path
    _ensure_seeded(path, "profile.example.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Profile.model_validate(data)


def save_profile(profile: Profile, path: Path | None = None) -> None:
    path = path or get_settings().profile_path
    path.write_text(yaml.safe_dump(profile.model_dump(), sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_search_config(path: Path | None = None) -> SearchConfig:
    path = path or get_settings().search_config_path
    _ensure_seeded(path, "search.example.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SearchConfig.model_validate(data)


def save_search_config(cfg: SearchConfig, path: Path | None = None) -> None:
    path = path or get_settings().search_config_path
    path.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False, allow_unicode=True), encoding="utf-8")


def parse_yaml_text(text: str, model: type[BaseModel]) -> BaseModel:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")
    return model.model_validate(data)
