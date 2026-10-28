from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Job
from tests.helpers import ingest, posting


def jobs(db):
    return list(db.scalars(select(Job).order_by(Job.id)))


def test_same_source_id_updates_not_duplicates(db):
    ingest(db, posting())
    st = ingest(db, posting(description=posting().description + "\nNew line"))
    assert st.updated == 1 and st.new == 0
    assert len(jobs(db)) == 1


def test_cross_source_duplicate_detected_by_fingerprint(db):
    ingest(db, posting())
    st = ingest(db, posting(provider="lever", board="acme", source_job_id="lv-9", title="Software Engineer",
                            source_url="https://jobs.lever.co/acme/lv-9", location="Bangalore"), provider="lever")
    assert st.duplicates == 1
    all_jobs = jobs(db)
    assert all_jobs[1].duplicate_of_id == all_jobs[0].id


def test_duplicate_detected_by_canonical_url(db):
    ingest(db, posting(source_url="https://acme.com/careers/job?gh_jid=55&utm_source=li"))
    st = ingest(db, posting(provider="manual", source_job_id="m1", title="SWE (Backend)", location="Hyderabad",
                            source_url="https://acme.com/careers/job/?gh_jid=55"), provider="manual")
    assert st.duplicates == 1


def test_location_hard_constraints(db):
    st = ingest(db,
                posting(source_job_id="us", location="San Francisco, CA, United States"),
                posting(source_job_id="remote-us", location="Remote - US"),
                posting(source_job_id="remote-fr", location="Paris, France", workplace_hint="remote", country_hint="France"),
                posting(source_job_id="remote-any", location="Remote"),
                posting(source_job_id="remote-in", location="Remote, India"),
                posting(source_job_id="pune", location="Pune"))
    ids = {j.source_job_id for j in jobs(db)}
    assert ids == {"remote-in", "pune", "remote-any"} and st.ineligible == 3


def test_closure_detected_only_from_complete_listings(db):
    ingest(db, posting(source_job_id="1"), posting(source_job_id="2", title="Backend Engineer"), complete=True)
    # incomplete listing missing job 2 -> no closure inferred
    ingest(db, posting(source_job_id="1"), complete=False, seen=["1"])
    assert all(j.listing_status == "open" for j in jobs(db))
    # complete listing missing job 2 -> closed
    st = ingest(db, posting(source_job_id="1"), complete=True, seen=["1"])
    assert st.closed == 1
    j2 = [j for j in jobs(db) if j.source_job_id == "2"][0]
    assert j2.listing_status == "closed" and j2.pipeline_status == "closed" and "No longer listed" in j2.closed_reason


def test_closure_keeps_submitted_status(db):
    ingest(db, posting(source_job_id="1"), complete=True)
    j = jobs(db)[0]
    j.pipeline_status = "submitted"
    db.commit()
    ingest(db, complete=True, seen=[])
    db.refresh(j)
    assert j.listing_status == "closed" and j.pipeline_status == "submitted"


def test_relisted_job_reopens(db):
    ingest(db, posting(source_job_id="1"), complete=True)
    ingest(db, complete=True, seen=[])
    ingest(db, posting(source_job_id="1"), complete=True)
    assert jobs(db)[0].listing_status == "open" and jobs(db)[0].pipeline_status == "discovered"


def test_stale_postings_skipped(db):
    old = datetime.now(timezone.utc) - timedelta(days=200)
    st = ingest(db, posting(source_job_id="old", posted_at=old))
    assert st.too_old == 1 and not jobs(db)


def test_invalid_data_without_posted_date_is_kept_unverified(db):
    ingest(db, posting(posted_at=None, posted_at_evidence=""))
    j = jobs(db)[0]
    assert j.posted_at is None and j.posted_at_evidence == ""
