"""Deterministic parsers for experience, salary, location and seniority.

Nothing here invents data: when a value cannot be found it is returned as
None / "unknown" and is surfaced as unverified in the UI.
"""
import re
from dataclasses import dataclass

INDIA_CITIES = {
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru", "hyderabad": "Hyderabad", "pune": "Pune",
    "chennai": "Chennai", "gurugram": "Gurugram", "gurgaon": "Gurugram", "noida": "Noida",
    "mumbai": "Mumbai", "delhi": "Delhi", "new delhi": "Delhi", "kolkata": "Kolkata",
    "ahmedabad": "Ahmedabad", "kochi": "Kochi", "trivandrum": "Thiruvananthapuram",
    "thiruvananthapuram": "Thiruvananthapuram", "jaipur": "Jaipur", "coimbatore": "Coimbatore",
    "indore": "Indore", "chandigarh": "Chandigarh", "mysore": "Mysuru", "mysuru": "Mysuru",
}
INDIA_MARKERS = re.compile(r"\b(india|ind|karnataka|telangana|maharashtra|tamil nadu|haryana|uttar pradesh|kerala)\b", re.I)
REMOTE_MARKERS = re.compile(r"\b(remote|work from home|wfh|anywhere|distributed)\b", re.I)
HYBRID_MARKERS = re.compile(r"\bhybrid\b", re.I)
COUNTRY_RESTRICTION = re.compile(
    r"(must (?:be|reside|live) (?:based |located )?in|only (?:open|available) to (?:candidates|applicants) (?:in|from)|"
    r"(?:us|u\.s\.|united states|eu|uk|canada)[- ]based (?:candidates|applicants) only|authori[sz]ed to work in the (?:us|united states|uk|eu))",
    re.I,
)


@dataclass
class LocationInfo:
    cities: list[str]
    in_india: bool
    is_remote: bool
    work_mode: str  # remote|hybrid|onsite|unknown
    raw: str


def parse_location(*parts: str, workplace_hint: str = "") -> LocationInfo:
    raw = " | ".join(p for p in parts if p)
    low = raw.lower()
    cities = []
    for key, city in INDIA_CITIES.items():
        if re.search(r"\b" + re.escape(key) + r"\b", low) and city not in cities:
            cities.append(city)
    in_india = bool(cities) or bool(INDIA_MARKERS.search(raw))
    hint = workplace_hint.lower()
    if "remote" in hint or REMOTE_MARKERS.search(raw):
        mode = "remote"
    elif "hybrid" in hint or HYBRID_MARKERS.search(raw):
        mode = "hybrid"
    elif hint in ("onsite", "on-site", "on_site", "office"):
        mode = "onsite"
    else:
        mode = "unknown"
    return LocationInfo(cities=cities, in_india=in_india, is_remote=mode == "remote", work_mode=mode, raw=raw)


_YEARS = re.compile(
    r"(?P<min>\d{1,2})(?:\.\d)?\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(?P<max>\d{1,2})\s*\+?)?\s*(?:years?|yrs?)\b(?P<ctx>[^.\n]{0,80})",
    re.I,
)
_EXP_CONTEXT = re.compile(r"(experience|exp\b|industry|professional|software|engineering|development|working)", re.I)


def parse_min_years(text: str) -> tuple[float | None, str]:
    """Return (minimum years required, evidence snippet). Uses the smallest
    plausible 'N+ years ... experience' requirement found."""
    best: float | None = None
    snippet = ""
    for m in _YEARS.finditer(text or ""):
        start = max(0, m.start() - 60)
        ctx = text[start : m.end()]
        if not _EXP_CONTEXT.search(ctx):
            continue
        if re.search(r"(company|founded|history|anniversary|old|age)", m.group("ctx") or "", re.I):
            continue
        val = float(m.group("min"))
        if val > 25:
            continue
        if best is None or val < best:
            best = val
            snippet = " ".join(ctx.split())[:200]
    return best, snippet


_LEVEL = r"(?:engineer|developer|sde|mts)\s*[-–,]?\s*"

SENIORITY_RULES = [
    (re.compile(r"\b(distinguished|fellow|vp|vice president|director|head of)\b", re.I), 0.0, "executive"),
    (re.compile(r"\b(principal|staff|architect)\b", re.I), 0.1, "staff+"),
    (re.compile(r"\b(lead|manager)\b", re.I), 0.25, "lead"),
    (re.compile(r"\b(senior|sr\.?|iii|level 3|l5|sde\s*-?\s*3|sde-?iii)\b|" + _LEVEL + r"(?:3|iii|iv|4)\b", re.I), 0.45, "senior"),
    (re.compile(r"\b(new grad|graduate|entry|junior|jr\.?|associate|early career|sde\s*-?\s*(?:1|i)\b|engineer\s*(?:1|i)\b|l3)\b|"
                + _LEVEL + r"(?:1|i)\b", re.I), 1.0, "entry"),
    (re.compile(r"\b(ii|2|sde\s*-?\s*2|l4|mid)\b", re.I), 0.9, "mid"),
]


def seniority_fit(title: str) -> tuple[float, str]:
    for pat, score, label in SENIORITY_RULES:
        if pat.search(title):
            return score, label
    return 0.8, "unspecified"


# ---------------- salary -----------------

@dataclass
class Salary:
    min: float | None = None
    max: float | None = None
    currency: str = ""
    period: str = ""  # year|month|hour
    kind: str = "unknown"  # base|total|estimated|unknown
    source: str = ""
    text: str = ""


_LPA = re.compile(r"(?:₹|inr|rs\.?)?\s*(\d{1,3}(?:\.\d+)?)\s*(?:-|–|to)\s*(\d{1,3}(?:\.\d+)?)\s*(?:lpa|lakhs?(?:\s*per\s*annum)?|l\b)", re.I)
_LPA_SINGLE = re.compile(r"(?:₹|inr|rs\.?)\s*(\d{1,3}(?:\.\d+)?)\s*(?:lpa|lakhs?)", re.I)
_MONEY_RANGE = re.compile(
    r"(?P<cur>\$|usd|€|eur|£|gbp|₹|inr|cad|aud|sgd)\s?(?P<a>\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?\s*[kK]?)\s*(?:-|–|to)\s*(?:\$|usd|€|eur|£|gbp|₹|inr|cad|aud|sgd)?\s?(?P<b>\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?\s*[kK]?)(?P<tail>[^.\n]{0,60})",
    re.I,
)
_CUR = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "₹": "INR", "inr": "INR",
        "cad": "CAD", "aud": "AUD", "sgd": "SGD"}


def _num(v: str) -> float:
    v = v.replace(",", "").replace(" ", "").lower()
    mult = 1000 if v.endswith("k") else 1
    return float(v.rstrip("k")) * mult


def parse_salary_text(text: str) -> Salary:
    """Extract a disclosed salary range from free text. Returns kind='unknown' when absent."""
    if not text:
        return Salary()
    m = _LPA.search(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return Salary(lo * 100000, hi * 100000, "INR", "year", _kind_from_context(text, m.start()), "description_text", m.group(0).strip())
    m = _LPA_SINGLE.search(text)
    if m:
        v = float(m.group(1)) * 100000
        return Salary(v, v, "INR", "year", _kind_from_context(text, m.start()), "description_text", m.group(0).strip())
    m = _MONEY_RANGE.search(text)
    if m:
        try:
            lo, hi = _num(m.group("a")), _num(m.group("b"))
        except ValueError:
            return Salary()
        if lo < 1000 and hi < 1000:  # likely hourly or not salary
            period = "hour" if re.search(r"(hour|hr)", m.group("tail") or "", re.I) else ""
            if not period:
                return Salary()
        else:
            period = "month" if re.search(r"month", m.group("tail") or "", re.I) else "year"
        cur = _CUR.get(m.group("cur").lower(), m.group("cur").upper())
        return Salary(lo, hi, cur, period, _kind_from_context(text, m.start()), "description_text", m.group(0).strip()[:200])
    return Salary()


def _kind_from_context(text: str, pos: int) -> str:
    ctx = text[max(0, pos - 160) : pos + 160].lower()
    if re.search(r"(total (?:comp|compensation|rewards)|\bott\b|\bctc\b|including (?:equity|bonus|stock))", ctx):
        return "total"
    if re.search(r"(estimate|estimated|glassdoor|levels\.fyi)", ctx):
        return "estimated"
    return "base"


def annual_lpa(sal: Salary, fx_to_inr: dict[str, float]) -> tuple[float | None, float | None]:
    """Convert a salary to annual INR lakhs (approximate for non-INR)."""
    if sal.min is None and sal.max is None:
        return None, None
    rate = 1.0 if sal.currency in ("INR", "") else fx_to_inr.get(sal.currency)
    if rate is None:
        return None, None
    mult = {"year": 1, "month": 12, "hour": 2080, "": 1}.get(sal.period, 1)

    def conv(v):
        return None if v is None else round(v * mult * rate / 100000, 1)

    return conv(sal.min), conv(sal.max)
