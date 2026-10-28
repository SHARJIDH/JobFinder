"""Browser assisted-fill tests against a local fixture form (skipped if Chromium is not installed)."""
from pathlib import Path

import pytest

from app.core.yaml_config import Profile
from app.services.browser_fill import FillReport, fill_form, is_supported

FORM = """<html><body><form>
<label for="first_name">First Name</label><input id="first_name" type="text">
<label for="last_name">Last Name</label><input id="last_name" type="text">
<label for="email">Email</label><input id="email" type="email">
<label for="li">LinkedIn Profile</label><input id="li" type="text">
<label for="q1">What is your notice period?</label><input id="q1" type="text">
<label for="q2">Salary expectations</label><input id="q2" type="text">
<label for="resume">Resume/CV</label><input id="resume" type="file">
<button type="submit" id="submit">Submit application</button>
</form><script>document.querySelector('form').addEventListener('submit', e => {e.preventDefault(); window.SUBMITTED = true;});</script>
</body></html>"""


def test_supported_hosts_only():
    assert is_supported("https://job-boards.greenhouse.io/acme/jobs/1")
    assert is_supported("https://jobs.lever.co/acme/abc")
    assert not is_supported("https://www.linkedin.com/jobs/view/1")
    assert not is_supported("http://jobs.lever.co/acme/abc")
    assert not is_supported("file:///etc/passwd")


def _browser():
    pw = pytest.importorskip("playwright.sync_api")
    try:
        p = pw.sync_playwright().start()
        b = p.chromium.launch(headless=True)
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Chromium not available: {e}")
    return p, b


def test_fill_form_never_submits_and_skips_unverified(tmp_path: Path):
    p, b = _browser()
    try:
        html = tmp_path / "form.html"
        html.write_text(FORM)
        resume = tmp_path / "resume.pdf"
        resume.write_bytes(b"%PDF-1.4 test")
        page = b.new_page()
        page.goto(html.as_uri())
        prof = Profile.model_validate({"contact": {"full_name": "Test Candidate", "email": "t@example.com",
                                                   "linkedin": "https://linkedin.com/in/t"}})
        answers = [{"question": "What is your notice period?", "answer": "30 days", "include": True, "verified": True, "needs_input": False},
                   {"question": "Salary expectations", "answer": "50 LPA", "include": True, "verified": False, "needs_input": False}]
        rep = FillReport(url=html.as_uri())
        fill_form(page, prof, answers, resume, None, rep)
        assert page.input_value("#first_name") == "Test" and page.input_value("#last_name") == "Candidate"
        assert page.input_value("#email") == "t@example.com"
        assert page.input_value("#q1") == "30 days"
        assert page.input_value("#q2") == ""  # unverified answer never auto-filled
        assert rep.uploaded and page.evaluate("window.SUBMITTED === undefined")
    finally:
        b.close()
        p.stop()


def test_captcha_stops_filling(tmp_path: Path):
    p, b = _browser()
    try:
        html = tmp_path / "c.html"
        html.write_text(FORM.replace("<form>", "<form><div class='g-recaptcha' data-sitekey='x'></div>"))
        page = b.new_page()
        page.goto(html.as_uri())
        rep = FillReport(url="x")
        fill_form(page, Profile(), [], None, None, rep)
        assert "CAPTCHA" in rep.blocked and page.input_value("#email") == ""
    finally:
        b.close()
        p.stop()
