"""End-to-end daily pipeline with mocked HTTP sources (no network, no LLM)."""
import httpx
from sqlalchemy import select

from app.core.yaml_config import SearchConfig, load_search_config, save_search_config
from app.models import Job, Run
from app.services.pipeline import run_daily
from app.sources.http import PoliteClient


def test_run_daily_end_to_end(db, test_profile):
    cfg = load_search_config()
    cfg = SearchConfig.model_validate(cfg.model_dump() | {"companies": [
        {"name": "Acme", "priority": 1, "sources": [{"provider": "greenhouse", "board": "acme"}]},
        {"name": "Broken", "priority": 2, "sources": [{"provider": "lever", "board": "broken"}]},
        {"name": "ManualOnly", "careers_url": "https://example.com/careers"},
    ]})
    save_search_config(cfg)
    gh = {"jobs": [
        {"id": 1, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
         "location": {"name": "Hyderabad, India"}, "first_published": "2026-10-26T00:00:00Z",
         "content": "Minimum qualifications: Python, SQL, REST APIs. 1+ years of experience."},
        {"id": 2, "title": "Backend Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
         "location": {"name": "New York, United States"}, "content": "Python"},
    ]}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if "greenhouse" in req.url.host:
            return httpx.Response(200, json=gh)
        return httpx.Response(500)

    http = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    run = run_daily(db, http=http, llm=None, notify=False)
    assert run.status == "partial"  # Lever source failed but run completed
    jobs = db.scalars(select(Job)).all()
    assert [j.source_job_id for j in jobs] == ["1"]  # US job filtered by location constraint
    j = jobs[0]
    assert j.score is not None and j.tier
    if j.tier == "A":
        assert j.pipeline_status == "resume_ready"
    assert db.scalars(select(Run)).first().finished_at is not None
