# Repository audit (Phase 1)

Audited on 2026-10-27 by cloning each repository and reading its README, licence, dependency
manifests and core source files.

| | A — Insideofcub/ai-job-hunter | B — torontodeveloper/job-application-agent | C — vaibhavarora14/job-application-agent |
|---|---|---|---|
| Status | v0.6.3, 1 contributor, first published ~1 week ago | ~4 months since last commit, no releases | Very active (v3.8.4, commits 3 days ago, 164★) |
| **Licence** | **MIT** | **None** (no LICENSE file → all rights reserved) | **MIT** |
| Stack | FastAPI + SQLAlchemy + SQLite; React/Vite SPA (single 1.5k-line `App.jsx`); Playwright | Python scripts + Playwright + OpenAI-compatible LLM | Node.js "Agent Skill" + CLI for coding agents; Cloudflare Workers |
| Model providers | Ollama (default), OpenAI-compatible | OpenAI-compatible (`LLM_API_KEY`) | None in CLI; the host coding agent does the reasoning |
| Discovery | Greenhouse, Lever, public feeds (Arbeitnow, RemoteOK…), Adzuna, Brave, optional JobSpy scraping | `discover.py`, `yc_discover.py` | Shared catalogue + community registry |
| Matching | Keyword + optional Ollama embeddings | — | Deterministic `score` CLI |
| Resume tailoring / PDF | No | Yes (`tailor.py`, Markdown → Chromium PDF) | No (one canonical résumé by design) |
| Approval before submit | `AUTO_SUBMIT_ENABLED=false` default | Fills form, user clicks Submit | Approval tokens for the exact item; “filled ≠ applied” |
| Windows/macOS | Windows-first scripts | Any | Any |

## Decision

**Primary foundation: Repository A (MIT)** — it is the only one with the same shape as the requested
product (FastAPI backend + database + web dashboard + public-ATS connectors) and a compatible licence.
However, its 1,840-line `main.py`, single-file frontend, absent migrations, absent auth and absent
Gemini/Ashby/scheduling support meant that extending it in place would cost more than restructuring.
The new codebase therefore **keeps A's architecture and adapts its Greenhouse/Lever connector logic**
(credited in `backend/app/sources/providers.py`) while rebuilding modules cleanly.

**Repository C (MIT)** — reused as *design patterns*, not code (it is a Node.js agent skill, a different runtime):
- approval bound to the exact reviewed content (here: `approval_hash` over documents + answers);
- "filled ≠ applied": submissions are recorded only with explicit evidence;
- pause on CAPTCHA/SSO/legal attestations; never read browser cookies.

**Repository B (no licence)** — **no code copied.** Only publicly described ideas were used at the
concept level: a single profile file as the source of truth, and "browser fills the form, human clicks
Submit". Its truthful-tailoring rules (no invented employers/metrics) are re-expressed independently and,
unlike B, enforced by deterministic validators rather than prompt text alone.

## Security / privacy concerns found

| Repo | Concern | How this project handles it |
|---|---|---|
| A | No authentication; README warns not to expose it | Password login, signed HttpOnly SameSite=Strict cookie, CSRF header, login rate limiting |
| A | Optional JobSpy scraping of LinkedIn/Indeed/Naukri | Not included; only documented public ATS endpoints + robots-checked Workday JSON |
| B | Persistent browser profile with logged-in cookies | Fresh browser context each time; no stored sessions |
| B | Prompt-only truthfulness rules | Deterministic validators reject unsupported numbers/skills/claims |
| C | Telemetry, contact sharing and community sharing **on by default** | No telemetry; nothing leaves your machine/project except Gemini calls you enable |

## Licences of new dependencies
FastAPI (MIT), SQLAlchemy (MIT), Alembic (MIT), Pydantic (MIT), httpx (BSD), PyYAML (MIT), pypdf (BSD),
python-docx (MIT), ReportLab (BSD), itsdangerous (BSD), google-genai (Apache-2.0), Playwright (Apache-2.0),
Next.js (MIT), React (MIT), Tailwind CSS (MIT), lucide-react (ISC).
