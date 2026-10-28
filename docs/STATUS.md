# Feature status, known limitations and future work

Legend: ✅ implemented **and** exercised by automated tests or a live run in this environment ·
🟡 implemented, verified only partially · ⛔ not implemented.

## Implemented

| Area | Feature | Verification |
|---|---|---|
| Discovery | Greenhouse, Lever, Ashby connectors (public, documented APIs) | ✅ unit tests (mocked) + live runs (Databricks, Stripe, OpenAI, Sarvam AI, Meesho) |
| | Workday public career-site JSON with robots.txt check, capped detail fetches | ✅ unit test + live run (NVIDIA: 25 relevant India jobs) |
| | **Google** Careers (first results page per query, newest first — pagination is disallowed by robots.txt) | ✅ unit test + live run |
| | **Microsoft** and **Qualcomm** via Eightfold `/api/pcsx` (explicitly allowed in their robots.txt) | ✅ unit test + live run |
| | **Amazon** via amazon.jobs search JSON | ✅ unit test + live run (245 India hits) |
| | **Atlassian** careers listings JSON (full list → closures detected) | ✅ unit test + live run |
| | **Oracle** via Oracle Recruiting Cloud REST | ✅ unit test + live run |
| | **Intuit** via sitemap + schema.org JobPosting on each job page | ✅ unit test + live run |
| | **Web search** (Gemini + Grounding with Google Search) for companies without a readable feed — **Uber, Walmart Global Tech, ServiceNow** by default; only Google-returned URLs on official domains are used | ✅ unit test + live run (Uber 3, Walmart 3, ServiceNow 9 leads) |
| | Unverified leads → one-click "paste description" import; JSON-LD "Fetch details" for manual adds | ✅ API tests |
| | RFC 9309 robots.txt evaluation (longest match wins), 401/403 never bypassed | ✅ tests |
| | SmartRecruiters connector (opt-in; its API robots.txt disallows generic agents) | 🟡 unit-level only; disabled by default |
| | Manual "Add job" for career sites without a permitted feed | ✅ API test |
| | Per-host rate limiting, retries with backoff, 401/403 → stop (never bypass) | ✅ tests |
| Normalization | Skills (required vs preferred), min years, salary (base/total/estimated/unknown), location, seniority | ✅ tests |
| Dedup | Source id; canonical URL; company+title+location fingerprint across sources; identical reposts on one board | ✅ tests |
| Expiry | Closure inferred only from *complete* listings; per-job verification endpoint; reopen on relist | ✅ tests |
| Ranking | 7 weighted, explainable factors; hard exclusions (location, experience gap, PhD/Master's/clearance); tiers A/B/C/D/X; opportunity-aware ordering | ✅ tests + live run |
| AI | Gemini via Vertex AI (`google-genai`), structured output + Pydantic validation, retry on invalid JSON, cache, daily call & spend caps, usage log | ✅ tests (fake provider) + live calls |
| Tailoring | Bullet rewrites tied to source ids; rejects new numbers/tools, unverified skills, inflated years; cover letter checks; deterministic fallback | ✅ tests + live Gemini run |
| Documents | ATS-friendly DOCX + PDF, traceability (resume sha, profile hash, JD hash, model, prompt version, timestamp) | ✅ tests |
| LaTeX | `.tex` resume upload as template (unsafe commands refused); Gemini applies the tailored plan to it; Tectonic compile in a temp dir; built-in LaTeX template fallback; browser editor (CodeMirror + server-rendered page previews, ⌘S recompile); one-page fit (layout tightening/trimming, or Gemini with page screenshots); "Ask Gemini" edits with fact validation and one compile-repair round; Open in Overleaf | ✅ tests (real Tectonic compile + mocked model) + live run (tailor, edit, AI edit with repair) |
| Profile | Editable YAML profile + search settings; **multiple labelled resumes** (include/exclude, rename, delete) as combined skill evidence; **free-text context notes** used as candidate-provided facts; AI "build profile from all resumes & notes" that merges projects/roles across documents with source attribution and never removes what you wrote | ✅ tests (AI build tested with a fake model; not run live) |
| Workflow | State machine; workspace with answers, checklist, concerns; approval bound to content hash; edits revoke approval; submission requires approval + evidence | ✅ tests |
| Browser | Playwright assisted fill for Greenhouse/Lever/Ashby hosts; CAPTCHA stop; never clicks Submit; only verified answers filled | ✅ tests against local fixture form; 🟡 not run against a live ATS form |
| Notifications | Daily digest (email SMTP / Telegram), only new A/B jobs, attention items, failure logging | ✅ tests (mocked Telegram); 🟡 email not sent live |
| Automation | `run-daily` pipeline, CLI, background runs from the dashboard, run & event logs | ✅ end-to-end test with mocked sources + live discovery/ranking |
| Dashboard | Overview, filterable jobs table, job detail (score breakdown, AI analysis, JD, documents, verification), tracker, application workspace, profile, settings, activity/costs | ✅ built, lint/type-checked, smoke-tested in a browser |
| Security | Password login, signed HttpOnly SameSite=Strict cookie, CSRF header, rate-limited login, security headers/CSP, upload validation, prompt-injection delimiting + detection, secrets via env/Secret Manager | ✅ tests for auth/CSRF/uploads/injection flagging |
| Deployment | Dockerfile (Tectonic + Litestream), idempotent dry-run-by-default GCP script: Cloud Run (max 1 instance, scale to zero) + SQLite replicated to GCS + Scheduler with OIDC-verified trigger + Secret Manager + least-privilege SAs | ✅ entrypoint restore/replication tested locally, scheduler endpoint unit-tested; not deployed (you chose local use) |
| Local run | `./start.sh`: one-command setup + start on macOS/Linux (installs missing tools, creates `.env`, builds, migrates, opens browser); auto-refresh on start when data is older than `AUTO_REFRESH_HOURS` | ✅ tested on a clean copy of the project (fresh `.env`, venv, node_modules, build, live discovery) |
| PostgreSQL | SQLAlchemy/Alembic models portable to Postgres | ✅ migrations + full test suite pass on local Postgres 15 (`TEST_DATABASE_URL`) |

## Known limitations

- **Uber, Walmart Global Tech and ServiceNow** block automated reading of their job pages (403 bot protection or
  robots.txt) — this is never bypassed. They are found via Google Search grounding and stored as *unverified
  leads* (title, location, official link); paste the description from the official page to rank and tailor them.
  Web search finds what Google has indexed, so it can lag new postings by days and is not exhaustive.
- Google Careers only allows the first results page per query (pagination is disallowed), so each daily run sees
  the newest ~20 postings per search term; several role-specific terms are used to widen coverage.
- Search-based connectors (Workday, Eightfold, Amazon, Oracle, Google) cannot prove a job closed by absence;
  active jobs are re-verified individually.
- Skill extraction uses a curated taxonomy (~90 skills); unusual technologies are not recognised until added
  to `core/skills.py`.
- Salary disclosure for India roles is rare; most jobs show "Not disclosed" (neutral score, flagged).
- Your current profile is the template: no experience bullets, projects or verified skills yet, so scores are
  conservative and tailored resumes will be sparse until you upload your resume and confirm facts.
- Application answers cover common questions; employer-specific custom questions must be added manually.
- The Gemini price table is configurable but not fetched live; estimates may drift from actual billing.
- Single-user by design; no multi-tenant isolation.
- LaTeX templates must be a single `.tex` file (no custom `.cls`, images or `\input` files). Fonts/packages come from
  Tectonic's bundle; very unusual packages may be missing. Overleaf edits are not synced back.
- The Tectonic install in the Dockerfile has not been built here (Docker unavailable locally).

## Future improvements

- More permitted sources (e.g. official company JSON feeds where terms allow, SmartRecruiters if you opt in).
- Embedding-based semantic pre-filter (Vertex text embeddings) to cut AI match calls further.
- Calibrate weights from your own outcomes (interview vs rejection).
- Deadline reminders and follow-up nudges in the digest.
- IAP-fronted deployment template; Postgres CI job.
