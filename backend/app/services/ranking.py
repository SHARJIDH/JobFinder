"""Explainable job ranking: deterministic scoring + optional AI semantic blend."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..core.parsing import Salary, annual_lpa, parse_location, seniority_fit
from ..core.skills import canonical_skill
from ..core.text import loads, sha256_text
from ..core.yaml_config import Profile, SearchConfig
from ..models import Job, aware

RANKING_VERSION = "r3"

INTEREST_KEYWORDS = {
    "backend": [r"backend", r"back-end", r"api", r"microservice", r"distributed"],
    "full-stack": [r"full[- ]?stack", r"frontend", r"react", r"next\.?js"],
    "ai": [r"\bai\b", r"machine learning", r"\bllm", r"generative", r"\bml\b"],
    "agentic": [r"agent", r"agentic", r"autonomous", r"tool[- ]use", r"orchestration"],
    "cloud": [r"cloud", r"aws", r"azure", r"gcp", r"kubernetes", r"serverless"],
    "software": [r"software", r"engineer", r"develop"],
}

MANDATORY_QUAL_PATTERNS = [
    (re.compile(r"\b(ph\.?d\.?)\b[^.\n]{0,40}\b(required|mandatory|must)\b", re.I), "PhD required", ("phd", "ph.d", "doctor")),
    (re.compile(r"\b(master'?s|m\.?s\.?|m\.?tech)\b[^.\n]{0,30}\b(degree )?(is )?(required|mandatory)\b", re.I), "Master's degree required", ("master", "m.tech", "m.s", "msc", "m.sc")),
    (re.compile(r"\b(security clearance|clearance required|ts/sci)\b", re.I), "Security clearance required", ()),
]


@dataclass
class Component:
    key: str
    label: str
    value: float  # 0..1
    weight: float
    detail: str

    @property
    def points(self) -> float:
        return round(self.value * self.weight * 100, 1)


@dataclass
class ScoreResult:
    deterministic: float
    final: float
    tier: str
    rank_value: float
    hard_excluded: bool
    exclusion_reasons: list[str]
    components: list[Component]
    flags: list[str]
    matched_required: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    matched_preferred: list[str] = field(default_factory=list)
    missing_preferred: list[str] = field(default_factory=list)
    annual_lpa: tuple[float | None, float | None] = (None, None)
    ai_score: float | None = None

    def breakdown(self) -> dict:
        return {
            "version": RANKING_VERSION,
            "deterministic": self.deterministic,
            "ai_score": self.ai_score,
            "final": self.final,
            "tier": self.tier,
            "components": [asdict(c) | {"points": c.points} for c in self.components],
            "flags": self.flags,
            "skills": {
                "matched_required": self.matched_required, "missing_required": self.missing_required,
                "matched_preferred": self.matched_preferred, "missing_preferred": self.missing_preferred,
            },
            "annual_lpa": list(self.annual_lpa),
        }


def score_input_hash(job: Job, profile: Profile, cfg: SearchConfig, resume_sha: str) -> str:
    return sha256_text(RANKING_VERSION, job.content_hash, profile.fingerprint(), cfg.fingerprint(), resume_sha,
                       job.listing_status)


def _role_family(title: str, cfg: SearchConfig) -> tuple[float, str]:
    t = title.lower().replace("-", " ")
    best, name = 0.3, "no configured role family"
    for fam in cfg.roles:
        if any(re.search(r"(?<![a-z0-9])" + re.escape(k.lower().replace("-", " ")) + r"(?![a-z0-9])", t) for k in fam.keywords):
            v = 1.0 if fam.priority == "primary" else 0.7
            if v > best:
                best, name = v, fam.name
    return best, name


def _interest_overlap(text: str, interests: list[str]) -> float:
    if not interests:
        return 0.5
    keys = set()
    for it in interests:
        low = it.lower()
        for k in INTEREST_KEYWORDS:
            if k.split("-")[0] in low:
                keys.add(k)
    if not keys:
        return 0.5
    hits = sum(1 for k in keys if any(re.search(p, text, re.I) for p in INTEREST_KEYWORDS[k]))
    return min(1.0, hits / max(2, len(keys) / 2))


def _freshness(job: Job, cfg: SearchConfig, now: datetime) -> tuple[float, str, bool]:
    posted = aware(job.posted_at)
    if posted:
        hours = (now - posted).total_seconds() / 3600
        if hours <= 24:
            v = 1.0
        elif hours <= cfg.freshness.preferred_hours:
            v = 0.9
        elif hours <= 24 * 7:
            v = 0.65
        elif hours <= 24 * 14:
            v = 0.45
        elif hours <= 24 * 30:
            v = 0.3
        else:
            v = 0.15
        return v, f"posted {hours / 24:.1f} days ago ({job.posted_at_evidence or 'source date'})", True
    seen = aware(job.first_seen_at) or now
    days = (now - seen).total_seconds() / 86400
    return 0.5 if days <= 3 else 0.3, f"posting date not provided by source; first seen {days:.1f} days ago", False


def _compensation(job: Job, cfg: SearchConfig) -> tuple[float, str, tuple, bool]:
    sal = Salary(job.salary_min, job.salary_max, job.salary_currency, job.salary_period, job.salary_kind)
    lo, hi = annual_lpa(sal, cfg.compensation.fx_to_inr)
    target = cfg.compensation.target_annual_lpa
    if lo is None and hi is None:
        return 0.5, "compensation not disclosed (unverified, neutral score)", (None, None), False
    top = hi if hi is not None else lo
    bottom = lo if lo is not None else hi
    approx = "" if job.salary_currency in ("INR", "") else f" (≈ converted from {job.salary_currency})"
    kind = {"base": "base", "total": "total comp", "estimated": "estimated"}.get(job.salary_kind, job.salary_kind)
    if bottom >= target:
        v = 1.0
    elif top >= target:
        v = 0.85
    elif top >= 0.8 * target:
        v = 0.55
    else:
        v = 0.25
    if job.salary_kind == "estimated":
        v = 0.5 + (v - 0.5) * 0.5
    return v, f"{kind} ₹{bottom}–{top} LPA{approx} vs target ₹{target} LPA", (lo, hi), True


def _company(job: Job, cfg: SearchConfig) -> tuple[float, str]:
    c = cfg.company(job.company)
    if not c:
        return 0.5, "not on watchlist"
    return {1: 1.0, 2: 0.8, 3: 0.65}[c.priority], f"watchlist priority {c.priority}"


def _location(job: Job, cfg: SearchConfig) -> tuple[float, str]:
    locs = [job.location, *loads(job.locations_json, [])]
    info = parse_location(*locs, workplace_hint=job.work_mode)
    pref = set(cfg.locations.preferred_cities)
    if info.in_india and set(info.cities) & pref:
        return 1.0, f"preferred city: {', '.join(sorted(set(info.cities) & pref))}"
    if info.in_india and info.is_remote:
        return 0.95, "remote within India"
    if info.in_india:
        return 0.75, "India, outside preferred cities" if info.cities else "India (city unspecified)"
    if info.is_remote:
        return 0.6, "remote (verify region eligibility)"
    if any(c.lower() in " ".join(locs).lower() for c in cfg.locations.international_eligible_countries):
        return 0.5, "international (eligible)"
    return 0.2, "location outside stated preferences"


def _experience(job: Job, cand_years: float, cfg: SearchConfig) -> tuple[float, str, str | None]:
    sen_v, sen_label = seniority_fit(job.title)
    excl = None
    if job.min_years_required is not None:
        gap = job.min_years_required - cand_years
        if gap <= 0:
            yv = 1.0
        elif gap <= 1:
            yv = 0.75
        elif gap <= 2:
            yv = 0.5
        elif gap <= 3:
            yv = 0.25
        else:
            yv = 0.05
        if gap >= cfg.experience.hard_exclude_years_gap:
            excl = f"requires {job.min_years_required:g}+ years; you have ~{cand_years:g}"
        v = 0.65 * yv + 0.35 * sen_v
        return v, f"requires {job.min_years_required:g}+ yrs (you: ~{cand_years:g}); title level: {sen_label}", excl
    return 0.85 * sen_v + 0.15 * 0.7, f"years not stated; title level: {sen_label}", None


def _skills(job: Job, usable: list[str], cfg: SearchConfig):
    have = {canonical_skill(s).lower() for s in usable}
    req = loads(job.required_skills_json, [])
    pref = loads(job.preferred_skills_json, [])
    mr = [s for s in req if s.lower() in have]
    xr = [s for s in req if s.lower() not in have]
    mp = [s for s in pref if s.lower() in have]
    xp = [s for s in pref if s.lower() not in have]
    if not req and not pref:
        return 0.5, "no recognisable skills in description (neutral)", mr, xr, mp, xp
    share = cfg.weights.required_skill_share
    rv = len(mr) / len(req) if req else 0.7
    pv = len(mp) / len(pref) if pref else 0.7
    v = share * rv + (1 - share) * pv
    return v, f"required {len(mr)}/{len(req)}, preferred {len(mp)}/{len(pref)}", mr, xr, mp, xp


def _mandatory_quals(job: Job, profile: Profile) -> list[str]:
    edu = " ".join(f"{e.degree} {e.institution}" for e in profile.education).lower()
    out = []
    for pat, label, satisfiers in MANDATORY_QUAL_PATTERNS:
        if pat.search(job.description or "") and not any(s in edu for s in satisfiers):
            # "or equivalent experience" softens a degree requirement
            m = pat.search(job.description)
            tail = job.description[m.end(): m.end() + 80].lower()
            if "equivalent" in tail or " or " in tail[:20]:
                continue
            out.append(label)
    return out


def _location_eligibility(job: Job, cfg: SearchConfig):
    from ..sources.base import RawPosting
    from .discovery import location_eligibility

    p = RawPosting(provider=job.provider, board=job.board, source_job_id=job.source_job_id, company=job.company,
                   title=job.title, source_url=job.source_url, location=job.location,
                   locations=loads(job.locations_json, []), country_hint=job.country,
                   workplace_hint="remote" if job.is_remote else job.work_mode)
    return location_eligibility(p, cfg)


def tier_for(score: float, cfg: SearchConfig, excluded: bool) -> str:
    if excluded:
        return "X"
    t = cfg.tiers
    return "A" if score >= t.a else "B" if score >= t.b else "C" if score >= t.c else "D"


TIER_BASE = {"A": 4000, "B": 3000, "C": 2000, "D": 1000, "X": 0}


def score_job(job: Job, profile: Profile, cfg: SearchConfig, usable: list[str], *, ai_score: float | None = None,
              now: datetime | None = None) -> ScoreResult:
    now = now or datetime.now(timezone.utc)
    w = cfg.weights
    flags: list[str] = []
    exclusions: list[str] = []
    cand_years = profile.years_of_experience(now.date())

    sk_v, sk_d, mr, xr, mp, xp = _skills(job, usable, cfg)
    fam_v, fam_name = _role_family(job.title, cfg)
    int_v = _interest_overlap(f"{job.title}\n{job.description[:6000]}", profile.interests)
    role_v = 0.7 * fam_v + 0.3 * int_v
    exp_v, exp_d, exp_excl = _experience(job, cand_years, cfg)
    comp_v, comp_d, lpa, comp_known = _compensation(job, cfg)
    co_v, co_d = _company(job, cfg)
    loc_v, loc_d = _location(job, cfg)
    fr_v, fr_d, date_known = _freshness(job, cfg, now)

    comps = [
        Component("skills", "Technical skills", sk_v, w.skills, sk_d),
        Component("role_fit", "Role & responsibilities", role_v, w.role_fit, f"{fam_name}; interest overlap {int_v:.0%}"),
        Component("experience", "Experience eligibility", exp_v, w.experience, exp_d),
        Component("compensation", "Compensation", comp_v, w.compensation, comp_d),
        Component("company", "Company preference", co_v, w.company, co_d),
        Component("location", "Location", loc_v, w.location, loc_d),
        Component("freshness", "Posting freshness", fr_v, w.freshness, fr_d),
    ]
    total_w = sum(c.weight for c in comps) or 1.0
    det = round(sum(c.value * c.weight for c in comps) / total_w * 100, 1)

    if exp_excl:
        exclusions.append(exp_excl)
    elig = _location_eligibility(job, cfg)
    if not elig.eligible:
        exclusions.append(elig.reason)
    exclusions += [f"mandatory qualification: {q}" for q in _mandatory_quals(job, profile)]
    if fam_v <= 0.3 and job.provider != "manual":
        flags.append("title does not match a configured role family")
    if not comp_known:
        flags.append("salary unverified")
    if not date_known:
        flags.append("posting date unverified")
    if job.listing_status == "closed":
        flags.append("listing closed")
    if loc_v <= 0.6:
        flags.append("verify location eligibility")

    final = det if ai_score is None else round((1 - w.ai_weight) * det + w.ai_weight * ai_score, 1)
    excluded = bool(exclusions)
    tier = tier_for(final, cfg, excluded)
    opportunity = (comp_v + co_v + fr_v) / 3
    rank_value = TIER_BASE[tier] + round(0.75 * final + 25 * opportunity, 2)
    return ScoreResult(det, final, tier, rank_value, excluded, exclusions, comps, flags, mr, xr, mp, xp, lpa, ai_score)
