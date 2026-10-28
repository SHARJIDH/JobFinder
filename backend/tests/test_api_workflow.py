import io

from docx import Document as Docx
from sqlalchemy import select

from app.models import Job
from tests.helpers import ingest, posting


def _setup_job(db, test_profile):
    ingest(db, posting())
    return db.scalars(select(Job)).first()


def test_api_requires_auth():
    from fastapi.testclient import TestClient

    from app.main import create_app

    c = TestClient(create_app())
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/api/health").status_code == 200
    assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401


def test_scheduler_endpoint_requires_google_token_for_configured_account(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    c = TestClient(create_app())
    assert c.post("/api/internal/run-daily").status_code == 404  # disabled unless configured

    monkeypatch.setenv("CRON_INVOKER_EMAIL", "sched@p.iam.gserviceaccount.com")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://jh.example")
    get_settings.cache_clear()
    seen = {}

    def fake_verify(token, _req, audience):
        seen["aud"] = audience
        if token != "good" and token != "other":
            raise ValueError("bad token")
        return {"email": "sched@p.iam.gserviceaccount.com" if token == "good" else "x@y.com", "email_verified": True}

    class FakeRun:
        id, status = 7, "success"

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", fake_verify)
    monkeypatch.setattr("app.api.system.run_daily", lambda db, notify: FakeRun())
    assert c.post("/api/internal/run-daily").status_code == 401
    assert c.post("/api/internal/run-daily", headers={"Authorization": "Bearer other"}).status_code == 403
    r = c.post("/api/internal/run-daily", headers={"Authorization": "Bearer good"})
    assert r.status_code == 200 and r.json() == {"run": 7, "status": "success"}
    assert seen["aud"] == "https://jh.example/api/internal/run-daily"
    get_settings.cache_clear()


def test_auto_refresh_only_when_stale(db, monkeypatch):
    import threading
    from datetime import timedelta

    from app.api import system
    from app.models import Run, utcnow

    done = threading.Event()
    monkeypatch.setattr(system, "run_daily", lambda _db, notify: done.set())
    assert system.maybe_auto_refresh(0) is False
    assert system.maybe_auto_refresh(20) is True and done.wait(5)  # never ran before
    for _ in range(50):
        if not system._run_lock.locked():
            break
        threading.Event().wait(0.05)
    db.add(Run(kind="daily", status="success", started_at=utcnow() - timedelta(hours=2)))
    db.commit()
    assert system.maybe_auto_refresh(20) is False
    db.add(Run(kind="daily", status="success", started_at=utcnow() - timedelta(hours=30)))
    db.query(Run).filter(Run.started_at > utcnow() - timedelta(hours=3)).delete()
    db.commit()
    done.clear()
    assert system.maybe_auto_refresh(20) is True and done.wait(5)

def test_csrf_header_required_for_mutations(client):
    client.headers.pop("X-Requested-With")
    r = client.post("/api/runs/rank")
    assert r.status_code == 403


def test_full_review_and_approval_flow(client, db, test_profile):
    job = _setup_job(db, test_profile)
    r = client.post(f"/api/jobs/{job.id}/prepare")
    assert r.status_code == 200, r.text
    app_id = r.json()["application_id"]
    detail = client.get(f"/api/applications/{app_id}").json()
    assert detail["job"]["pipeline_status"] == "ready_for_review"
    assert detail["documents"]["resume"]["content"]["experience"]
    # blockers present: unchecked checklist and answers needing input
    assert any("Checklist" in b for b in detail["blockers"])
    assert any("Answer needed" in b for b in detail["blockers"])

    # unauthorized submission (not approved) is refused
    r = client.post(f"/api/applications/{app_id}/submitted", json={"evidence": "Thanks for applying - ref 123"})
    assert r.status_code == 409
    # approval refused while blockers remain
    assert client.post(f"/api/applications/{app_id}/approve").status_code == 409

    answers = detail["application"]["answers"]
    for a in answers:
        if a["needs_input"]:
            if a["key"].startswith("narrative"):
                a["answer"] = "I enjoy building backend services."
            else:
                a["include"] = False  # not asked on this form
        a["verified"] = True
    r = client.put(f"/api/applications/{app_id}", json={"answers": answers,
                                                        "checklist": {k: True for k in detail["checklist_labels"]}})
    assert r.status_code == 200
    r = client.post(f"/api/applications/{app_id}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["job"]["pipeline_status"] == "approved"

    # editing after approval revokes it
    answers[0]["answer"] = "changed"
    client.put(f"/api/applications/{app_id}", json={"answers": answers})
    assert client.get(f"/api/applications/{app_id}").json()["job"]["pipeline_status"] == "ready_for_review"
    assert client.post(f"/api/applications/{app_id}/submitted", json={"evidence": "Confirmation #9981"}).status_code == 409

    # re-approve, then record submission with evidence
    assert client.post(f"/api/applications/{app_id}/approve").status_code == 200
    assert client.post(f"/api/applications/{app_id}/submitted", json={"evidence": "short"}).status_code == 422
    r = client.post(f"/api/applications/{app_id}/submitted", json={"evidence": "Confirmation email: Application received #9981"})
    assert r.status_code == 200 and r.json()["job"]["pipeline_status"] == "submitted"
    # outcomes
    r = client.post(f"/api/applications/{app_id}/outcome", json={"status": "interview"})
    assert r.json()["job"]["pipeline_status"] == "interview"
    assert len(r.json()["application"]["history"]) >= 6


def test_status_endpoint_cannot_skip_approval(client, db, test_profile):
    job = _setup_job(db, test_profile)
    assert client.post(f"/api/jobs/{job.id}/status", json={"status": "submitted"}).status_code == 400
    assert client.post(f"/api/jobs/{job.id}/status", json={"status": "interview"}).status_code == 409
    assert client.post(f"/api/jobs/{job.id}/status", json={"status": "shortlisted"}).status_code == 200


def test_generate_and_download_documents(client, db, test_profile):
    job = _setup_job(db, test_profile)
    r = client.post(f"/api/jobs/{job.id}/generate", json={"use_ai": False, "cover_letter": True})
    assert r.status_code == 200
    doc_id = r.json()["resume"]["id"]
    pdf = client.get(f"/api/documents/{doc_id}/download?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "Acme" in pdf.headers["content-disposition"]
    docx = client.get(f"/api/documents/{doc_id}/download?format=docx")
    text = "\n".join(p.text for p in Docx(io.BytesIO(docx.content)).paragraphs)
    assert "Kyndryl India" in text and "IIT Tirupati" in text
    assert client.get(f"/api/documents/{doc_id}/download?format=exe").status_code == 422
    assert client.get(f"/api/jobs/{job.id}").json()["pipeline_status"] == "resume_ready"


def _docx_bytes(text: str) -> bytes:
    d = Docx()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_resume_upload_validation_and_evidence(client, test_profile):
    bad = client.post("/api/profile/resume", files={"file": ("resume.exe", b"MZ....", "application/octet-stream")})
    assert bad.status_code == 400
    fake_pdf = client.post("/api/profile/resume", files={"file": ("resume.pdf", b"not a pdf at all", "application/pdf")})
    assert fake_pdf.status_code == 400 and "valid PDF" in fake_pdf.json()["detail"]
    content = _docx_bytes("Test Candidate\nSkills: Python, FastAPI, React, Docker\nExperience at Kyndryl India building APIs with Python " * 3)
    ok = client.post("/api/profile/resume", files={"file": ("resume.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert ok.status_code == 200, ok.text
    data = ok.json()
    ev = {e["canonical"]: e for e in data["skill_evidence"]}
    assert ev["React"]["in_resume"] and ev["React"]["usable"]  # unverified in profile but evidenced by resume
    assert not ev["Kubernetes"]["usable"]
    assert "Docker" in data["resume_suggestions"]


def test_profile_yaml_validation(client, test_profile):
    r = client.put("/api/profile", json={"yaml": "skills: not-a-list"})
    assert r.status_code == 422
    r = client.put("/api/profile", json={"yaml": "- just\n- a list"})
    assert r.status_code == 422


def test_settings_update_and_validation(client):
    s = client.get("/api/settings").json()
    cfg = s["config"]
    cfg["compensation"]["target_annual_lpa"] = 40
    assert client.put("/api/settings", json={"config": cfg}).status_code == 200
    assert client.get("/api/settings").json()["config"]["compensation"]["target_annual_lpa"] == 40
    cfg["tiers"] = {"a": "high"}
    assert client.put("/api/settings", json={"config": cfg}).status_code == 422


def test_manual_job_and_listing_filters(client, test_profile):
    r = client.post("/api/jobs/manual", json={
        "company": "Google", "title": "Software Engineer, Early Career", "url": "https://www.google.com/about/careers/applications/jobs/results/123",
        "location": "Hyderabad, India", "description": "Minimum qualifications:\n- Python, SQL, 1 year of experience in software development." * 2})
    assert r.status_code == 200, r.text
    assert client.post("/api/jobs/manual", json={"company": "X", "title": "Y", "url": "http://insecure", "description": "x" * 60}).status_code == 422
    lst = client.get("/api/jobs?company=Google&min_score=1").json()
    assert lst["total"] == 1 and lst["items"][0]["salary"]["kind"] == "unknown"
    assert client.get("/api/jobs?salary=known").json()["total"] == 0


def test_overview_and_usage(client):
    o = client.get("/api/overview").json()
    assert "counts" in o and any(m["name"] == "Uber" for m in o["manual_watch"])
    assert not any(m["name"] == "Google" for m in o["manual_watch"])  # Google now has an official feed
    u = client.get("/api/usage").json()
    assert u["budget"]["provider"] == "disabled"


def test_delete_personal_data(client, db, test_profile):
    job = _setup_job(db, test_profile)
    client.post(f"/api/jobs/{job.id}/generate", json={"use_ai": False})
    r = client.delete("/api/profile/data?scope=documents")
    assert r.status_code == 200
    assert client.get(f"/api/jobs/{job.id}").json()["documents"] == []
