from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.text import loads
from app.core.yaml_config import load_search_config
from app.models import Job
from app.services.pipeline import rank_jobs
from tests.helpers import ingest, posting


def ranked(db, test_profile, *postings):
    ingest(db, *postings)
    rank_jobs(db, load_search_config(), test_profile, llm=None)
    return {j.source_job_id: j for j in db.scalars(select(Job))}


def test_score_has_explainable_breakdown(db, test_profile):
    j = ranked(db, test_profile, posting())["1"]
    bd = loads(j.score_breakdown_json)
    assert 0 <= j.score <= 100
    keys = {c["key"] for c in bd["components"]}
    assert keys == {"skills", "role_fit", "experience", "compensation", "company", "location", "freshness"}
    assert "Python" in bd["skills"]["matched_required"]
    assert j.tier in "ABCD"


def test_missing_salary_is_neutral_and_flagged_not_rejected(db, test_profile):
    j = ranked(db, test_profile, posting())["1"]
    bd = loads(j.score_breakdown_json)
    comp = [c for c in bd["components"] if c["key"] == "compensation"][0]
    assert comp["value"] == 0.5 and "salary unverified" in bd["flags"]
    assert not j.hard_excluded


def test_experience_gap_hard_exclusion(db, test_profile):
    jd = "Minimum qualifications:\n- 8+ years of software engineering experience\n- Python"
    j = ranked(db, test_profile, posting(description=jd, title="Software Engineer"))["1"]
    assert j.hard_excluded and j.tier == "X"
    assert "requires 8+ years" in loads(j.exclusion_reasons_json)[0]


def test_mandatory_phd_exclusion_but_equivalent_is_ok(db, test_profile):
    jobs = ranked(db, test_profile,
                  posting(source_job_id="phd", description="Minimum qualifications:\nPhD is required in CS. Python."),
                  posting(source_job_id="eq", title="Backend Engineer",
                          description="Minimum qualifications:\nMaster's degree required or equivalent experience. Python."))
    assert jobs["phd"].hard_excluded
    assert not jobs["eq"].hard_excluded


def test_company_prestige_does_not_dominate(db, test_profile):
    jobs = ranked(db, test_profile,
                  posting(source_job_id="fit", company="Tiny Startup"),
                  posting(source_job_id="prestige", company="Databricks", title="Staff Software Engineer",
                          description="Minimum qualifications:\n- Java, Scala, Spark\n"))
    assert jobs["fit"].score > jobs["prestige"].score


def test_freshness_and_unverified_dates(db, test_profile):
    jobs = ranked(db, test_profile,
                  posting(source_job_id="fresh"),
                  posting(source_job_id="nodate", title="Backend Engineer", posted_at=None, posted_at_evidence=""),
                  posting(source_job_id="old", title="Software Developer", posted_at=datetime.now(timezone.utc) - timedelta(days=40)))
    f = {k: [c for c in loads(v.score_breakdown_json)["components"] if c["key"] == "freshness"][0]["value"] for k, v in jobs.items()}
    assert f["fresh"] > f["nodate"] and f["fresh"] > f["old"]
    assert "posting date unverified" in loads(jobs["nodate"].score_breakdown_json)["flags"]


def test_unverified_skill_not_counted(db, test_profile):
    # React is declared but unverified in the test profile and there is no resume evidence
    j = ranked(db, test_profile, posting(title="Frontend Engineer", description="Required:\n- React\n- TypeScript"))["1"]
    assert "React" in loads(j.score_breakdown_json)["skills"]["missing_required"]


def test_rescoring_skips_unchanged(db, test_profile):
    ranked(db, test_profile, posting())
    st = rank_jobs(db, load_search_config(), test_profile, llm=None)
    assert st["scored"] == 0 and st["unchanged"] == 1
