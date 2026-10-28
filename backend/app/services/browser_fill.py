"""Assisted form filling with Playwright for supported ATS-hosted forms.

Safety properties (enforced in code):
  * Only hosts in SUPPORTED_HOSTS are opened.
  * Fields are filled only from the verified profile and approved answers.
  * The final submit button is NEVER clicked by this module. The user reviews
    the filled form in a visible browser window and clicks Submit personally.
  * If a CAPTCHA / bot challenge is detected, the run stops and reports it.
  * A fresh browser context is used (no cookies or saved sessions are read).
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..core.yaml_config import Profile

SUPPORTED_HOSTS = re.compile(r"(^|\.)((job-)?boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com)$", re.I)
CAPTCHA_SELECTORS = ["iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='turnstile']",
                     "div.g-recaptcha", "[data-sitekey]"]


@dataclass
class FillReport:
    url: str
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    blocked: str = ""
    note: str = "Browser left open for your review. The assistant never clicks Submit."


def is_supported(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return url.startswith("https://") and bool(SUPPORTED_HOSTS.search(host))


def application_url(url: str) -> str:
    host = urlsplit(url).hostname or ""
    if host.endswith("jobs.lever.co") and not url.rstrip("/").endswith("/apply"):
        return url.rstrip("/") + "/apply"
    if host.endswith("jobs.ashbyhq.com") and not url.rstrip("/").endswith("/application"):
        return url.rstrip("/") + "/application"
    return url


def field_values(profile: Profile, answers: list[dict]) -> dict[str, str]:
    c = profile.contact
    first, _, last = (c.full_name or "").strip().partition(" ")
    vals = {
        r"^first name": first, r"^last name": last, r"^(full )?name\b": c.full_name, r"e-?mail": c.email,
        r"phone": c.phone, r"linkedin": c.linkedin, r"github": c.github, r"(portfolio|website|personal site)": c.portfolio,
        r"(current )?location|city": c.location,
    }
    for a in answers:
        if a.get("include") and a.get("verified") and a.get("answer") and not a.get("needs_input"):
            vals[re.escape(a["question"][:60].lower())] = a["answer"]
    return {k: v for k, v in vals.items() if v}


def fill_form(page, profile: Profile, answers: list[dict], resume_path: Path | None, cover_path: Path | None,
              report: FillReport) -> None:
    for sel in CAPTCHA_SELECTORS:
        if page.locator(sel).count():
            report.blocked = "CAPTCHA / bot challenge detected — complete the form manually. Not bypassing."
            return
    values = field_values(profile, answers)
    inputs = page.locator("input[type=text], input[type=email], input[type=tel], input[type=url], input:not([type]), textarea")
    for i in range(inputs.count()):
        el = inputs.nth(i)
        try:
            if not el.is_visible() or el.input_value():
                continue
            label = _label_for(page, el)
        except Exception:
            continue
        low = label.lower()
        for pat, val in values.items():
            if re.search(pat, low):
                el.fill(val)
                report.filled.append(label[:80])
                break
        else:
            if label:
                report.skipped.append(label[:80])
    files = page.locator("input[type=file]")
    for i in range(files.count()):
        el = files.nth(i)
        label = (_label_for(page, el) + " " + (el.get_attribute("name") or "") + " " + (el.get_attribute("id") or "")).lower()
        if resume_path and re.search(r"resume|cv", label) and "resume" not in " ".join(report.uploaded):
            el.set_input_files(str(resume_path))
            report.uploaded.append(f"resume: {resume_path.name}")
        elif cover_path and re.search(r"cover", label):
            el.set_input_files(str(cover_path))
            report.uploaded.append(f"cover letter: {cover_path.name}")


def _label_for(page, el) -> str:
    el_id = el.get_attribute("id")
    if el_id:
        lab = page.locator(f"label[for='{el_id}']")
        if lab.count():
            return " ".join(lab.first.inner_text().split())
    aria = el.get_attribute("aria-label") or el.get_attribute("placeholder") or el.get_attribute("name") or ""
    if aria:
        return aria
    try:
        return " ".join(el.evaluate("e => (e.closest('label') || e.parentElement)?.innerText || ''").split())[:120]
    except Exception:
        return ""


_active: dict[int, threading.Thread] = {}


def launch_assisted_fill(app_id: int, url: str, profile: Profile, answers: list[dict], resume_path: Path | None,
                         cover_path: Path | None, headless: bool = False, wait_for_close: bool = True) -> FillReport:
    """Open a visible browser, fill supported fields and leave it open for the user."""
    if not is_supported(url):
        raise ValueError("This application site is not supported for assisted filling; open it manually.")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("Playwright is not installed (pip install '.[browser]' && playwright install chromium)") from e

    report = FillReport(url=application_url(url))
    ready = threading.Event()

    def run():
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=headless)
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(report.url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1500)
                fill_form(page, profile, answers, resume_path, cover_path, report)
                ready.set()
                if wait_for_close and not headless:
                    page.wait_for_event("close", timeout=0)
                browser.close()
        except Exception as e:  # report instead of crashing the API
            report.blocked = report.blocked or f"Browser automation error: {type(e).__name__}: {str(e)[:200]}"
            ready.set()
        finally:
            _active.pop(app_id, None)

    if app_id in _active:
        raise RuntimeError("An assisted-fill browser is already open for this application")
    t = threading.Thread(target=run, daemon=True)
    _active[app_id] = t
    t.start()
    ready.wait(timeout=90)
    return report
