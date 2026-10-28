import json

import httpx

from app.sources.http import PoliteClient
from app.sources.providers import FetchContext, fetch_source, verify_posting

CTX = FetchContext(role_keywords=["software engineer", "backend", "machine learning"], exclude_keywords=["intern", "director"])


def client(handler) -> PoliteClient:
    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)


def robots_ok(request):
    return httpx.Response(404) if request.url.path == "/robots.txt" else None


def test_greenhouse_parsing_and_title_filter():
    payload = {"jobs": [
        {"id": 11, "title": "Software Engineer, Backend", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11",
         "location": {"name": "Bengaluru, India"}, "first_published": "2026-10-25T10:00:00-04:00",
         "content": "&lt;p&gt;Python and Kubernetes. 2+ years of experience.&lt;/p&gt;"},
        {"id": 12, "title": "Sales Director", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12", "location": {"name": "X"}},
        {"id": 13, "title": "Software Engineering Intern", "absolute_url": "https://boards.greenhouse.io/acme/jobs/13", "location": {"name": "X"}},
    ]}

    def handler(req):
        return robots_ok(req) or httpx.Response(200, json=payload)

    res = fetch_source(client(handler), "greenhouse", "acme", "Acme", CTX)
    assert res.complete and not res.error
    assert [p.source_job_id for p in res.postings] == ["11"]
    assert res.seen_ids == {"11", "12", "13"}
    p = res.postings[0]
    assert p.posted_at is not None and p.posted_at_evidence == "greenhouse first_published"
    assert "Python" in p.description


def test_lever_salary_range_is_base_and_disclosed():
    payload = [{"id": "abc", "text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/acme/abc",
                "applyUrl": "https://jobs.lever.co/acme/abc/apply", "categories": {"location": "Pune", "commitment": "Full-time"},
                "createdAt": 1761400000000, "descriptionPlain": "Build APIs",
                "salaryRange": {"min": 3000000, "max": 4000000, "currency": "INR", "interval": "per-year-salary"}}]

    def handler(req):
        return robots_ok(req) or httpx.Response(200, json=payload)

    res = fetch_source(client(handler), "lever", "acme", "Acme", CTX)
    s = res.postings[0].salary
    assert (s.min, s.max, s.currency, s.period, s.kind) == (3000000, 4000000, "INR", "year", "base")


def test_ashby_unlisted_jobs_are_skipped_and_compensation_parsed():
    payload = {"jobs": [
        {"id": "a1", "title": "Machine Learning Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/a1", "isListed": True,
         "location": "Bengaluru", "publishedAt": "2026-10-26T00:00:00Z", "descriptionPlain": "PyTorch",
         "compensation": {"summaryComponents": [{"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "INR",
                                                 "minValue": 4000000, "maxValue": 5000000}]}},
        {"id": "a2", "title": "Backend Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/a2", "isListed": False},
    ]}

    def handler(req):
        return robots_ok(req) or httpx.Response(200, json=payload)

    res = fetch_source(client(handler), "ashby", "acme", "Acme", CTX)
    assert [p.source_job_id for p in res.postings] == ["a1"]
    assert "a2" not in res.seen_ids
    assert res.postings[0].salary.min == 4000000


def test_missing_board_is_reported_not_crashing():
    def handler(req):
        return robots_ok(req) or httpx.Response(404, json={})

    res = fetch_source(client(handler), "greenhouse", "nope", "Nope", CTX)
    assert not res.complete and "404" in res.error


def test_failed_api_call_is_captured():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("network down")

    res = fetch_source(client(handler), "lever", "acme", "Acme", CTX)
    assert not res.complete and "ConnectError" in res.error


def test_robots_disallow_blocks_fetch():
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        raise AssertionError("must not fetch disallowed URL")

    res = fetch_source(client(handler), "greenhouse", "acme", "Acme", CTX)
    assert "robots.txt" in res.error


def test_access_denied_is_not_bypassed():
    calls = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(req)
        return httpx.Response(403, text="captcha")

    res = fetch_source(client(handler), "greenhouse", "acme", "Acme", CTX)
    assert "access denied" in res.error and len(calls) == 1


def test_workday_detail_fetch_and_incomplete_listing():
    search = {"total": 1, "jobPostings": [{"title": "Software Engineer", "externalPath": "/job/India-Pune/SE_JR1",
                                           "locationsText": "India, Pune", "postedOn": "Posted 2 Days Ago"}]}
    detail = {"jobPostingInfo": {"title": "Software Engineer", "jobDescription": "<p>Python, 1+ years experience</p>",
                                 "location": "India, Pune", "startDate": "2026-10-25", "timeType": "Full time",
                                 "jobReqId": "JR1", "externalUrl": "https://acme.wd5.myworkdayjobs.com/Site/job/India-Pune/SE_JR1",
                                 "country": {"descriptor": "India"}, "posted": True}}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /refreshFacet/")
        if req.method == "POST":
            body = json.loads(req.content)
            assert "India" in body["searchText"]
            return httpx.Response(200, json=search)
        return httpx.Response(200, json=detail)

    res = fetch_source(client(handler), "workday", "acme.wd5.myworkdayjobs.com|acme|Site", "Acme", CTX, ["software engineer"])
    assert res.complete is False  # search results never prove closure
    assert res.postings[0].source_job_id == "JR1" and res.postings[0].posted_at_evidence == "workday startDate"


def test_verify_posting_closed_on_404():
    def handler(req):
        return robots_ok(req) or httpx.Response(404)

    assert verify_posting(client(handler), "greenhouse", "acme", "1", "") is False
