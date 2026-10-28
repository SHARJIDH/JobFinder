import json

import httpx
from sqlalchemy import select

from app.core.yaml_config import SearchConfig, load_search_config
from app.llm.service import LLMService, ProviderResponse
from app.models import Job
from app.services.pipeline import discover
from app.sources.http import PoliteClient, Robots
from app.sources.jsonld import extract_jobpostings, jobposting_to_raw
from app.sources.providers import FetchContext, fetch_source

CTX = FetchContext(role_keywords=["software engineer", "machine learning", "backend"], exclude_keywords=["intern"])


def client(handler) -> PoliteClient:
    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)


# ------------------------------------------------------------------ robots.txt (RFC 9309)

def test_robots_longest_match_wins():
    r = Robots("User-agent: *\nDisallow: /\nAllow: /$\nAllow: /api/pcsx\n", "AIJobHunter/0.1")
    assert r.allowed("/api/pcsx/search?x=1") and r.allowed("/") and not r.allowed("/secret")


def test_robots_agent_specific_group_and_wildcards():
    txt = ("User-agent: *\nDisallow: /results?page=\nDisallow: /*.pdf$\n\n"
           "User-agent: Yandex\nDisallow: /results\n")
    r = Robots(txt, "AIJobHunter/0.1")
    assert r.allowed("/results/123") and not r.allowed("/results?page=2") and not r.allowed("/a/b.pdf")
    assert not Robots(txt, "Yandex").allowed("/results/123")


# ------------------------------------------------------------------ JSON-LD

HTML = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},
{"@type":"JobPosting","title":"Backend Engineer","datePosted":"2026-10-20","validThrough":"2099-01-01",
"description":"<p>Python &amp; Kafka. 2+ years experience.</p>","identifier":{"@type":"PropertyValue","value":"J-77"},
"jobLocation":{"@type":"Place","address":{"addressLocality":"Pune","addressCountry":"IN"}},
"baseSalary":{"@type":"MonetaryAmount","currency":"INR","value":{"minValue":3000000,"maxValue":4000000,"unitText":"YEAR"}}}]}
</script></head></html>"""


def test_jsonld_jobposting_parsed_with_salary_and_date():
    jps = extract_jobpostings(HTML)
    p = jobposting_to_raw(jps[0], "https://jobs.example.com/job/77", "Acme", "web", "acme")
    assert p.title == "Backend Engineer" and p.location == "Pune, IN" and p.source_job_id == "J-77"
    assert p.posted_at.isoformat().startswith("2026-10-20") and p.posted_at_evidence.startswith("JSON-LD")
    assert (p.salary.min, p.salary.max, p.salary.kind) == (3000000, 4000000, "base")


def test_jsonld_expired_posting_ignored():
    jp = extract_jobpostings(HTML.replace("2099-01-01", "2020-01-01"))[0]
    assert jobposting_to_raw(jp, "https://x/1", "Acme", "web", "acme") is None


# ------------------------------------------------------------------ official-site connectors (mocked)

def _robots(req):
    return httpx.Response(404) if req.url.path == "/robots.txt" else None


def test_eightfold_search_and_details():
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\nAllow: /api/pcsx\n")
        if req.url.path.endswith("/search"):
            return httpx.Response(200, json={"data": {"positions": [
                {"id": 11, "displayJobId": "200001", "name": "Software Engineer II", "postedTs": 1793000000},
                {"id": 12, "displayJobId": "200002", "name": "Sales Specialist"}]}})
        return httpx.Response(200, json={"data": {"id": 11, "displayJobId": "200001", "name": "Software Engineer II",
                                                  "jobDescription": "<p>Python</p>", "locations": ["India, Hyderabad"],
                                                  "postedTs": 1793000000, "publicUrl": "https://careers.x.com/careers/job/11"}})

    res = fetch_source(client(h), "eightfold", "careers.x.com|x.com", "X", CTX, ["software engineer"])
    assert [p.source_job_id for p in res.postings] == ["200001"] and res.complete is False
    assert res.postings[0].posted_at is not None and res.postings[0].location == "India, Hyderabad"


def test_amazon_search_json():
    def h(req):
        return _robots(req) or httpx.Response(200, json={"hits": 1, "jobs": [{
            "id_icims": "10589519", "title": "Software Development Engineer", "posted_date": "October 27, 2026",
            "normalized_location": "Bengaluru, Karnataka, IND", "job_path": "/en/jobs/10589519/sde",
            "description": "Build services", "basic_qualifications": "- 1+ years of experience", "url_next_step": "https://account.amazon.jobs/jobs/10589519/apply"}]})

    ctx = FetchContext(role_keywords=["software development engineer"], exclude_keywords=[])
    res = fetch_source(client(h), "amazon", "IND", "Amazon", ctx, ["software development engineer"])
    p = res.postings[0]
    assert p.source_url == "https://www.amazon.jobs/en/jobs/10589519/sde" and p.posted_at.day == 27
    assert "Basic qualifications" in p.description


def test_atlassian_full_listing_enables_closure():
    def h(req):
        return _robots(req) or httpx.Response(200, json=[
            {"id": 1, "title": "Backend Software Engineer", "locations": ["Bengaluru - India"], "overview": "<p>Java</p>"},
            {"id": 2, "title": "Account Executive", "locations": ["Remote - India"]}])

    res = fetch_source(client(h), "atlassian", "atlassian", "Atlassian", CTX)
    assert res.complete and res.seen_ids == {"1", "2"} and [p.source_job_id for p in res.postings] == ["1"]
    assert res.postings[0].posted_at is None  # only an updatedDate is published: never used as posting date


def test_oracle_rest_connector():
    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if "recruitingCEJobRequisitions" in req.url.path:
            return httpx.Response(200, json={"items": [{"requisitionList": [
                {"Id": "344317", "Title": "Application Software Engineer 1", "PostedDate": "2026-10-27",
                 "PrimaryLocation": "BENGALURU, KARNATAKA, India", "PrimaryLocationCountry": "IN"}]}]})
        return httpx.Response(200, json={"items": [{"ExternalDescriptionStr": "<p>Java, SQL</p>"}]})

    res = fetch_source(client(h), "oracle", "h.example.com|CX_1|300", "Oracle", CTX, ["software engineer"])
    p = res.postings[0]
    assert p.source_job_id == "344317" and "Java" in p.description and p.posted_at.year == 2026
    assert p.source_url.endswith("/sites/CX_1/job/344317")


def test_google_careers_first_page_only():
    job = [123, "Software Engineer, Search", "x", [None, "<ul><li>Build</li></ul>"], [None, "<p>Python</p>"], None, None,
           "Google", "en", [["Bengaluru, Karnataka, India", []]], [None, "<p>About</p>"], None, [1793000000, 0]]
    page = "<script>AF_initDataCallback({key: 'ds:1', hash: '2', data:" + json.dumps([[job], None, 1, 20]) + ", sideChannel: {}});</script>"
    calls = []

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /about/careers/applications/jobs/results?page=\n")
        calls.append(str(req.url))
        return httpx.Response(200, text=page)

    res = fetch_source(client(h), "google", "India", "Google", CTX, ["software engineer"])
    assert len(calls) == 1 and "page=" not in calls[0]
    p = res.postings[0]
    assert p.source_job_id == "123" and p.location.startswith("Bengaluru") and "Python" in p.description
    assert p.source_url.endswith("/123-software-engineer-search")


def test_sitemap_jsonld_connector_and_known_ids():
    sitemap = ("<urlset><url><loc>https://jobs.x.com/job/bengaluru/software-engineer/1/101</loc></url>"
               "<url><loc>https://jobs.x.com/job/bengaluru/sales-manager/1/102</loc></url>"
               "<url><loc>https://jobs.x.com/job/new-york/software-engineer/1/103</loc></url></urlset>")
    fetched = []

    def h(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /search-jobs/\n")
        if req.url.path.endswith("sitemap.xml"):
            return httpx.Response(200, text=sitemap)
        fetched.append(req.url.path)
        return httpx.Response(200, text=HTML.replace("Backend Engineer", "Software Engineer"))

    board = "https://jobs.x.com/sitemap.xml|/job/(bengaluru|hyderabad)/"
    res = fetch_source(client(h), "sitemap", board, "X", CTX)
    assert res.complete and res.seen_ids == {"101", "102"} and fetched == ["/job/bengaluru/software-engineer/1/101"]
    ctx2 = FetchContext(role_keywords=CTX.role_keywords, exclude_keywords=[], known_ids={"101"})
    fetched.clear()
    fetch_source(client(h), "sitemap", board, "X", ctx2)
    assert fetched == []  # already-known jobs are not re-fetched


# ------------------------------------------------------------------ grounded web search

class GroundedFake:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def model_for(self, tier):
        return "fake"

    def generate(self, **kw):  # pragma: no cover - not used here
        raise AssertionError

    def grounded_generate(self, *, prompt, max_output_tokens):
        self.calls += 1
        text = ("Senior Software Engineer | Bengaluru, India\nSoftware Engineer II | Hyderabad, India\n"
                "Machine Learning Engineer | Bengaluru\nhttps://uber.com/fake/999999 | made up")
        sources = [{"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/a", "title": "uber.com"},
                   {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/b", "title": "uber.com"},
                   {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/c", "title": "instahyre.com"},
                   {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/d", "title": "uber.com"}]
        supports = [{"text": "Senior Software Engineer | Bengaluru, India", "chunks": [0]},
                    {"text": "Software Engineer II | Hyderabad, India", "chunks": [1, 2]},
                    {"text": "Machine Learning Engineer | Bengaluru", "chunks": [3]}]
        return ProviderResponse(text=text, input_tokens=100, output_tokens=50, model="fake"), sources, supports


def _web_cfg():
    cfg = load_search_config()
    return SearchConfig.model_validate(cfg.model_dump() | {"companies": [
        {"name": "Uber", "priority": 1, "careers_url": "https://www.uber.com/careers", "official_domains": ["uber.com"]}]})


def _web_http():
    targets = {"/grounding-api-redirect/a": "https://jobs.uber.com/en/jobs/301974",
               "/grounding-api-redirect/b": "https://jobs.uber.com/en/jobs/147313",
               "/grounding-api-redirect/c": "https://www.instahyre.com/job-426939-uber",
               "/grounding-api-redirect/d": "https://jobs.uber.com/en/jobs/555555"}
    hits = []

    def h(req):
        hits.append(str(req.url))
        if req.url.host == "vertexaisearch.cloud.google.com":
            return httpx.Response(302, headers={"location": targets[req.url.path]})
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path.endswith("/555555"):  # readable page with structured data -> verified posting
            return httpx.Response(200, text=HTML.replace("Backend Engineer", "Machine Learning Engineer").replace("Pune", "Bengaluru"))
        return httpx.Response(403, text="bot check")  # never bypassed -> lead

    return client(h), hits


def test_web_search_uses_only_grounded_official_sources(db, test_profile):
    http, hits = _web_http()
    fake = GroundedFake()
    stats = discover(db, _web_cfg(), http, llm=LLMService(db, provider=fake))
    jobs = {j.source_url: j for j in db.scalars(select(Job))}
    assert set(jobs) == {"https://jobs.uber.com/en/jobs/301974", "https://jobs.uber.com/en/jobs/147313",
                         "https://jobs.uber.com/en/jobs/555555"}
    assert all("instahyre" not in u and "999999" not in u for u in hits)  # aggregator ignored, model-typed URL never used
    lead = jobs["https://jobs.uber.com/en/jobs/301974"]
    assert lead.listing_status == "unverified" and lead.title == "Senior Software Engineer" and lead.score is None
    verified = jobs["https://jobs.uber.com/en/jobs/555555"]
    assert verified.listing_status == "open" and verified.provider == "web" and verified.posted_at is not None
    assert stats["web_leads"] == 2 and stats["web_verified"] == 1
    # cached for the day: a second run makes no new grounded call
    discover(db, _web_cfg(), http, llm=LLMService(db, provider=fake))
    assert fake.calls == 1


def test_import_description_turns_lead_into_ranked_job(client, db, test_profile):
    http, _ = _web_http()
    discover(db, _web_cfg(), http, llm=LLMService(db, provider=GroundedFake()))
    lead = db.scalars(select(Job).where(Job.listing_status == "unverified")).first()
    assert client.get("/api/jobs?listing=unverified").json()["total"] == 2
    r = client.post(f"/api/jobs/{lead.id}/import-description",
                    json={"description": "Minimum qualifications:\n- 2+ years of experience\n- Python, SQL and REST APIs\n" * 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["listing_status"] == "open" and body["score"] is not None and "Python" in body["required_skills"]


def test_web_search_disabled_without_llm(db, test_profile):
    http, hits = _web_http()
    stats = discover(db, _web_cfg(), http, llm=LLMService(db, provider=None))
    assert stats["web_leads"] == 0 and not hits
