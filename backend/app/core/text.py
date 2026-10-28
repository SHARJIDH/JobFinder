"""Text helpers: HTML → text, normalization, hashing, URL canonicalization."""
import hashlib
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    # Greenhouse returns entity-escaped HTML; unescape first.
    raw = unescape(value) if "&lt;" in value else value
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def norm(value: str) -> str:
    """Lowercase, strip punctuation, collapse spaces."""
    v = value.lower()
    v = re.sub(r"[^a-z0-9+#.\s]", " ", v)
    return re.sub(r"\s+", " ", v).strip()


_ROMAN = {" i": " 1", " ii": " 2", " iii": " 3", " iv": " 4"}
_TITLE_ALIASES = [
    (r"\bsr\b\.?", "senior"),
    (r"\bjr\b\.?", "junior"),
    (r"\bsde\b", "software development engineer"),
    (r"\bswe\b", "software engineer"),
    (r"\bml\b", "machine learning"),
    (r"\bfull[\s-]?stack\b", "fullstack"),
    (r"\bback[\s-]?end\b", "backend"),
    (r"\bfront[\s-]?end\b", "frontend"),
]


def normalize_title(title: str) -> str:
    t = " " + norm(title.replace("-", " ").replace("/", " ")) + " "
    for pat, rep in _TITLE_ALIASES:
        t = re.sub(pat, rep, t)
    for k, v in _ROMAN.items():
        t = re.sub(re.escape(k) + r"\b", v, t)
    return re.sub(r"\s+", " ", t).strip()


_CITY_ALIASES = {
    "bangalore": "bengaluru",
    "gurgaon": "gurugram",
    "new delhi": "delhi",
    "bombay": "mumbai",
    "madras": "chennai",
}


def normalize_location(loc: str) -> str:
    v = norm(loc)
    for a, b in _CITY_ALIASES.items():
        v = v.replace(a, b)
    v = re.sub(r"\b(india|in|ind|karnataka|telangana|maharashtra|tamil nadu|haryana|uttar pradesh)\b", " ", v)
    return re.sub(r"\s+", " ", v).strip()


_TRACKING_PARAMS = re.compile(r"^(utm_.*|gh_src|source|lever-source.*|ref|src|trk.*)$", re.I)


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/")
    for suffix in ("/apply", "/application"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))


def sha256_text(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()


def stable_json_hash(obj) -> str:
    return sha256_text(json.dumps(obj, sort_keys=True, default=str))


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(value: str | None, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
