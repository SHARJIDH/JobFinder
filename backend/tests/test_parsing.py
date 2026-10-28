from app.core.parsing import parse_location, parse_min_years, parse_salary_text, seniority_fit
from app.core.skills import extract_required_preferred, extract_skills
from app.core.text import canonical_url, html_to_text, normalize_title
from tests.helpers import JD_BACKEND


def test_html_to_text_handles_escaped_greenhouse_html():
    raw = "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(raw)
    assert "Hello" in text and "- Python" in text and "<" not in text


def test_normalize_title_equivalences():
    assert normalize_title("Sr. Software Engineer II") == normalize_title("Senior Software Engineer 2")
    assert normalize_title("Back-End Developer") == normalize_title("backend developer")


def test_canonical_url_strips_tracking_but_keeps_job_id():
    a = canonical_url("https://databricks.com/careers/job?gh_jid=123&utm_source=x&gh_src=abc")
    b = canonical_url("https://databricks.com/careers/job/?gh_jid=123")
    assert a == b and "gh_jid=123" in a
    assert canonical_url("https://jobs.lever.co/x/abc/apply") == canonical_url("https://jobs.lever.co/x/abc")


def test_required_vs_preferred_skills():
    req, pref = extract_required_preferred(JD_BACKEND)
    assert {"Python", "SQL", "REST APIs"} <= set(req)
    assert {"Kubernetes", "AWS", "LLMs"} <= set(pref)
    assert not set(req) & set(pref)


def test_skill_extraction_avoids_false_positives():
    assert "Java" not in extract_skills("Strong JavaScript skills")
    assert "Go" not in extract_skills("You will go to meetings and Go-to-market")
    assert "Go" in extract_skills("Languages: Python, Go, Rust")
    assert "Express" not in extract_skills("I express my keen interest")
    assert "REST APIs" not in extract_skills("work with the rest of the team")
    assert "REST APIs" in extract_skills("Design REST APIs and RESTful services")
    assert "Spark" not in extract_skills("spark curiosity")


def test_min_years():
    assert parse_min_years("Requires 3+ years of professional experience")[0] == 3
    assert parse_min_years("2-4 years experience in backend development")[0] == 2
    assert parse_min_years("Founded 25 years ago, our company...")[0] is None
    assert parse_min_years("No experience requirement stated")[0] is None


def test_salary_parsing_never_fabricates():
    s = parse_salary_text("Compensation: ₹30 - 45 LPA plus benefits")
    assert (s.min, s.max, s.currency, s.kind) == (3_000_000, 4_500_000, "INR", "base")
    s = parse_salary_text("The base salary range is $150,000 - $190,000 per year")
    assert s.currency == "USD" and s.min == 150000 and s.period == "year"
    s = parse_salary_text("Total compensation (CTC) 40-50 LPA")
    assert s.kind == "total"
    s = parse_salary_text("Competitive salary and great benefits")
    assert s.min is None and s.kind == "unknown"


def test_location_and_seniority():
    info = parse_location("Bangalore, Karnataka")
    assert info.in_india and info.cities == ["Bengaluru"]
    assert parse_location("Remote - India").is_remote
    assert seniority_fit("Staff Software Engineer")[0] < 0.2
    assert seniority_fit("Software Engineer I")[0] == 1.0
    assert seniority_fit("Senior Backend Engineer")[1] == "senior"
    assert seniority_fit("Software Development Engineer - 3, Tax")[1] == "senior"
    assert seniority_fit("Software Development Engineer I")[1] == "entry"
