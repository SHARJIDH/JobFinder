from sqlalchemy import select

from app.models import Job
from tests.helpers import ingest, posting


def test_same_board_distinct_requisitions_are_not_merged(db):
    ingest(db, posting(source_job_id="r1", description="Team A builds payments APIs in Python."),
           posting(source_job_id="r2", description="Team B builds search infrastructure in Go."))
    assert all(j.duplicate_of_id is None for j in db.scalars(select(Job)))


def test_same_board_identical_repost_is_merged(db):
    ingest(db, posting(source_job_id="r1"), posting(source_job_id="r2"))
    jobs = list(db.scalars(select(Job).order_by(Job.id)))
    assert jobs[1].duplicate_of_id == jobs[0].id
