from datetime import datetime, timedelta, timezone

from app.core.parsing import Salary
from app.core.yaml_config import load_search_config
from app.services.discovery import upsert_result
from app.sources.base import RawPosting, SourceResult

JD_BACKEND = """About the role
We are looking for a Software Engineer to build backend services.

Minimum qualifications:
- 1+ years of software engineering experience
- Strong Python and SQL skills
- Experience building REST APIs

Preferred qualifications:
- Experience with Kubernetes and AWS
- Familiarity with LLMs
"""


def posting(**kw) -> RawPosting:
    base = dict(provider="greenhouse", board="acme", source_job_id="1", company="Acme",
                title="Software Engineer", source_url="https://boards.greenhouse.io/acme/jobs/1",
                apply_url="https://boards.greenhouse.io/acme/jobs/1", location="Bengaluru, India",
                description=JD_BACKEND, posted_at=datetime.now(timezone.utc) - timedelta(hours=10),
                posted_at_evidence="test", salary=Salary())
    base.update(kw)
    if "source_url" not in kw:
        base["source_url"] = base["apply_url"] = f"https://boards.greenhouse.io/acme/jobs/{base['source_job_id']}"
    return RawPosting(**base)


def ingest(db, *postings, complete=False, provider="greenhouse", board="acme", seen=None, cfg=None):
    cfg = cfg or load_search_config()
    res = SourceResult(provider, board, "Acme", postings=list(postings), complete=complete)
    res.seen_ids = set(seen if seen is not None else [p.source_job_id for p in postings])
    stats = upsert_result(db, res, cfg)
    db.commit()
    return stats
