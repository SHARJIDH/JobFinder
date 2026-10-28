"""Deterministic skill taxonomy and extraction.

Used to (a) extract required/preferred skills from job descriptions and
(b) check which profile skills are evidenced by the master resume.
It is intentionally conservative: false negatives are preferable to
claiming a skill that is not actually present.
"""
import re
from functools import lru_cache

# canonical name -> list of alias regexes (matched case-insensitively unless noted)
SKILLS: dict[str, list[str]] = {
    # languages
    "Python": [r"python"],
    "Java": [r"java(?!\s*script)"],
    "JavaScript": [r"javascript", r"\bjs\b", r"ecmascript", r"es6"],
    "TypeScript": [r"typescript", r"\bts\b(?=[,/ )])"],
    "Go": [r"golang"],
    "C++": [r"c\+\+", r"\bcpp\b"],
    "C#": [r"c#", r"\.net core", r"csharp"],
    "C": [],
    "Rust": [r"\brust\b"],
    "Kotlin": [r"kotlin"],
    "Scala": [r"\bscala\b"],
    "Ruby": [r"\bruby\b"],
    "PHP": [r"\bphp\b"],
    "SQL": [r"\bsql\b"],
    "Bash": [r"\bbash\b", r"shell scripting"],
    # frontend
    "React": [r"react(?:\.js|js)?\b(?!\s*native)"],
    "React Native": [r"react native"],
    "Next.js": [r"next\.?js"],
    "Angular": [r"angular"],
    "Vue.js": [r"\bvue(?:\.js|js)?\b"],
    "HTML/CSS": [r"\bhtml5?\b", r"\bcss3?\b"],
    "Tailwind CSS": [r"tailwind"],
    "Redux": [r"\bredux\b"],
    # backend
    "Node.js": [r"node\.?js"],
    "Express": [r"express\.?js"],
    "FastAPI": [r"fastapi"],
    "Django": [r"django"],
    "Flask": [r"\bflask\b"],
    "Spring Boot": [r"spring\s*boot", r"spring framework"],
    "GraphQL": [r"graphql"],
    "REST APIs": [r"\brestful\b", r"\brest(?:ful)?\s*apis?\b"],
    "gRPC": [r"\bgrpc\b"],
    "Microservices": [r"micro-?services"],
    "Distributed Systems": [r"distributed systems?"],
    "System Design": [r"system design"],
    "Data Structures & Algorithms": [r"data structures", r"algorithms"],
    # data
    "PostgreSQL": [r"postgres(?:ql)?"],
    "MySQL": [r"mysql"],
    "MongoDB": [r"mongo(?:db)?"],
    "Redis": [r"\bredis\b"],
    "Elasticsearch": [r"elastic\s*search", r"opensearch"],
    "Kafka": [r"\bkafka\b"],
    "Spark": [r"pyspark", r"apache spark"],
    "Airflow": [r"airflow"],
    "DynamoDB": [r"dynamo\s*db"],
    "Cassandra": [r"cassandra"],
    "Snowflake": [r"snowflake"],
    "NoSQL": [r"no-?sql"],
    # cloud / infra
    "AWS": [r"\baws\b", r"amazon web services"],
    "Azure": [r"\bazure\b"],
    "GCP": [r"\bgcp\b", r"google cloud"],
    "Docker": [r"docker", r"containeri[sz]ation"],
    "Kubernetes": [r"kubernetes", r"\bk8s\b"],
    "Terraform": [r"terraform"],
    "CI/CD": [r"ci\s*/\s*cd", r"continuous (?:integration|delivery|deployment)", r"github actions", r"jenkins"],
    "Linux": [r"\blinux\b", r"\bunix\b"],
    "Git": [r"\bgit\b"],
    "Serverless": [r"serverless", r"aws lambda", r"cloud functions"],
    "Observability": [r"observability", r"prometheus", r"grafana", r"opentelemetry"],
    # AI / ML
    "Machine Learning": [r"machine learning", r"\bml\b"],
    "Deep Learning": [r"deep learning", r"neural networks?"],
    "PyTorch": [r"pytorch", r"\btorch\b"],
    "TensorFlow": [r"tensorflow", r"\bkeras\b"],
    "scikit-learn": [r"scikit-?learn", r"sklearn"],
    "NLP": [r"\bnlp\b", r"natural language processing"],
    "Computer Vision": [r"computer vision"],
    "LLMs": [r"\bllms?\b", r"large language models?", r"generative ai", r"\bgenai\b", r"\bgpt\b", r"gemini", r"claude"],
    "RAG": [r"\brag\b", r"retrieval[- ]augmented"],
    "AI Agents": [r"agentic", r"ai agents?", r"multi-agent", r"\bagents?\b(?= framework| orchestration)"],
    "LangChain": [r"langchain", r"langgraph"],
    "Prompt Engineering": [r"prompt engineering"],
    "Vector Databases": [r"vector (?:db|database|store)s?", r"pinecone", r"weaviate", r"pgvector", r"faiss", r"chroma"],
    "MLOps": [r"mlops", r"model deployment", r"kubeflow", r"mlflow"],
    "Vertex AI": [r"vertex ai"],
    "Pandas": [r"\bpandas\b"],
    "NumPy": [r"numpy"],
    # practices
    "Testing": [r"unit test(?:s|ing)?", r"\bpytest\b", r"\bjest\b", r"test-driven", r"\btdd\b"],
    "Agile": [r"\bagile\b", r"\bscrum\b"],
    "Security": [r"application security", r"\boauth\b", r"\bowasp\b"],
    "Networking": [r"tcp/ip", r"networking"],
    "Embedded Systems": [r"embedded systems?", r"firmware", r"\brtos\b"],
    "Signal Processing": [r"signal processing", r"\bdsp\b"],
    "MATLAB": [r"matlab"],
}

# patterns that are matched case-sensitively (ambiguous short words)
CASE_SENSITIVE: dict[str, list[str]] = {
    "Go": [r"\bGo\b(?![- ]to\b)(?=\s*(?:[,/)]|and\b|or\b|$))", r"\(Go\)"],
    "C": [r"(?<![\w+#])C(?=\s*(?:/|,)\s*C\+\+)", r"\bC/C\+\+"],
    "Node.js": [r"\bNode\b"],
    "Express": [r"\bExpress\b(?=\s*(?:[,/)]|and\b|or\b|framework|server))"],
    "Spring Boot": [r"\bSpring\b(?=\s*(?:[,/)]|and\b|or\b|framework|MVC))"],
    "REST APIs": [r"\bREST\b"],
    "Spark": [r"\bSpark\b"],
}


@lru_cache
def _compiled() -> list[tuple[str, list[re.Pattern]]]:
    out = []
    for name, aliases in SKILLS.items():
        pats = [re.compile(a, re.I) for a in aliases]
        pats += [re.compile(a) for a in CASE_SENSITIVE.get(name, [])]
        out.append((name, pats))
    return out


def canonical_skill(name: str) -> str:
    """Map a free-text skill name to the canonical taxonomy name when possible."""
    n = name.strip()
    for canon, pats in _compiled():
        if n.lower() == canon.lower():
            return canon
    for canon, pats in _compiled():
        if any(p.fullmatch(n) or p.fullmatch(n.lower()) for p in pats):
            return canon
    return n


def extract_skills(text: str) -> list[str]:
    if not text:
        return []
    found = []
    for name, pats in _compiled():
        if any(p.search(text) for p in pats):
            found.append(name)
    return found


def text_mentions_skill(text: str, skill: str) -> bool:
    canon = canonical_skill(skill)
    for name, pats in _compiled():
        if name == canon:
            return any(p.search(text) for p in pats) or skill.lower() in text.lower()
    return re.search(r"(?<!\w)" + re.escape(skill.lower()) + r"(?!\w)", text.lower()) is not None


_PREFERRED_HEAD = re.compile(
    r"(preferred|nice[- ]to[- ]have|bonus|plus|desired|good to have|additional qualifications|great if)", re.I
)
_REQUIRED_HEAD = re.compile(
    r"(minimum|basic|required|requirements|must[- ]have|qualifications|what you(?:'ll)? (?:need|bring)|you (?:have|bring)|who you are|about you|skills)",
    re.I,
)


def split_requirement_sections(text: str) -> tuple[str, str, str]:
    """Split JD text into (required, preferred, other) by heading heuristics."""
    required, preferred, other = [], [], []
    bucket = other
    for line in text.splitlines():
        stripped = line.strip()
        is_heading = (
            0 < len(stripped) <= 80
            and not stripped.startswith("- ")
            and (stripped.endswith(":") or stripped.istitle() or stripped.isupper() or len(stripped.split()) <= 6)
        )
        if is_heading and _PREFERRED_HEAD.search(stripped):
            bucket = preferred
        elif is_heading and _REQUIRED_HEAD.search(stripped):
            bucket = required
        elif is_heading and re.search(r"(about (?:us|the company)|benefits|perks|equal opportunity|why join)", stripped, re.I):
            bucket = other
        bucket.append(stripped)
    return "\n".join(required), "\n".join(preferred), "\n".join(other)


def extract_required_preferred(text: str) -> tuple[list[str], list[str]]:
    req_text, pref_text, other_text = split_requirement_sections(text)
    pref = extract_skills(pref_text)
    if req_text.strip():
        req = extract_skills(req_text)
    else:
        req = extract_skills(other_text)
    # Skills that appear in the required section are required even if also "preferred".
    pref = [s for s in pref if s not in req]
    return req, pref
