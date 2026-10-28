# Architecture, data model and migrations

```mermaid
flowchart LR
  subgraph Sources["Permitted public sources (robots.txt per RFC 9309)"]
    GH[Greenhouse / Lever / Ashby APIs]
    WD[Workday career-site JSON]
    OF[Official sites: Google Careers, Eightfold<br/>Microsoft+Qualcomm, amazon.jobs, Atlassian,<br/>Oracle REST, sitemap+JSON-LD Intuit]
    WS[Gemini + Google Search grounding<br/>official domains only → verified or lead]
    SR[SmartRecruiters API<br/>opt-in]
    MAN[Manual add<br/>official URL + JD]
  end
  Sources --> N[Normalize<br/>skills · years · salary · location]
  N --> D[Deduplicate<br/>source id · canonical URL · fingerprint]
  D --> X[Closure detection<br/>complete listings only]
  X --> R[Deterministic score<br/>7 weighted factors + hard exclusions]
  R -->|promising jobs, within budget| AI[Gemini fast model<br/>structured AIMatch]
  AI --> R2[Blend + tiers A/B/C]
  R2 --> T[Tailoring<br/>Gemini quality model]
  T --> V[Truthfulness validators]
  V --> DOC[DOCX + PDF<br/>with traceability]
  DOC --> W[Application workspace<br/>answers · checklist · concerns]
  W --> AP{Explicit approval<br/>hash of exact content}
  AP --> SUB[User submits on official site]
  SUB --> EV[Record evidence → Submitted]
  R2 --> NT[Daily digest<br/>email / Telegram]
```

## Code layout

```
backend/app/
  config.py            env settings (secrets, limits, model names)
  core/                pure logic: text, skills taxonomy, parsing, YAML schemas, safety, state machine
  sources/             http.py (rate limit, robots, no-bypass), providers.py (connectors), base.py
  services/            discovery, ranking, tailoring, render, applications, browser_fill, notify, pipeline
  llm/                 service.py (provider, cache, budget, usage), tasks.py (schemas + prompts)
  api/                 FastAPI routers (system/auth, jobs, applications, profile)
  cli.py               migrate | run-daily | discover | rank | notify
frontend/              Next.js App Router (static export) + Tailwind; served by FastAPI in production
config/                committed templates for profile.yaml and search.yaml
data/                  (git-ignored) profile.yaml, search.yaml, SQLite DB, uploads, documents
```

Deterministic code owns rules, deduplication, validation and every state change. The model is used
only for (a) semantic fit assessment and (b) wording, and all of its output is schema-validated and
then checked against your verified facts.

## Database schema

| Table | Purpose | Key columns |
|---|---|---|
| `jobs` | One row per source posting | `provider`+`source_job_id` (unique), `canonical_url`, `fingerprint`, `duplicate_of_id`, posting fields, `required_skills_json`, `preferred_skills_json`, `min_years_required`, salary (`salary_min/max/currency/period/kind/source/text`), `posted_at` + `posted_at_evidence`, `first_seen_at`, `last_verified_at`, `listing_status`, `pipeline_status`, `score`, `deterministic_score`, `ai_score`, `tier`, `rank_value`, `score_breakdown_json`, `ai_analysis_json`, `hard_excluded`, `exclusion_reasons_json`, `score_input_hash`, `notified_at` |
| `master_resumes` | Uploaded resumes (several can be included at once) | `label`, `sha256` (duplicates rejected), `storage_path`, extracted `text`, `is_active` = included as evidence |
| `documents` | Generated resumes / cover letters | `job_id`, `kind`, `generation_id`, `content_json`, `docx_path`, `pdf_path`, **traceability**: `source_resume_id`, `source_resume_sha256`, `profile_hash`, `job_content_hash`, `generator`, `model`, `prompt_version`, `validation_json`, `created_at` |
| `applications` | Workspace per job | `answers_json`, `checklist_json`, `concerns_json`, `approved_at`, `approval_hash`, `submitted_at`, `submission_method`, `submission_evidence`, `deadline`, `notes`, `history_json` |
| `llm_usage` | Every model call (incl. cache hits/failures) | `task`, `model`, tokens, `est_cost_usd`, `cached`, `success`, `error` |
| `llm_cache` | Response cache | `key` = sha256(task, prompt version, model, prompt, schema) |
| `runs` | Pipeline runs | `kind`, `status`, `stats_json` |
| `events` | Activity & error log | `level`, `category`, `message`, `job_id`, `run_id` |
| `notifications` | Digest deliveries | `channel`, `status`, `job_ids_json`, `error` |

### Salary semantics
`salary_kind` is one of `base` (stated base pay), `total` (CTC / total compensation), `estimated`
(explicitly labelled estimate) or `unknown` (nothing disclosed). Nothing is ever inferred.
Non-INR amounts are converted to annual LPA with the configurable `fx_to_inr` table and labelled "≈".

### Workflow states
`discovered → shortlisted → resume_ready → ready_for_review → approved → submitted → interview/offer/rejected`,
plus `closed` and `archived`. Allowed transitions are enforced in `core/states.py`. `approved` and
`submitted` can only be reached through the approval and evidence endpoints.

## Migrations

Alembic is the source of truth for production schemas.

```bash
cd backend
.venv/bin/python -m app.cli migrate                    # upgrade to head (also runs on server start)
DATA_DIR=/tmp/x .venv/bin/alembic revision --autogenerate -m "describe change"   # after editing models.py
```

`DATABASE_URL` selects the database: empty → SQLite at `DATA_DIR/jobhunter.db`; for PostgreSQL use
`postgresql+psycopg://user:pass@host:5432/jobhunter` (install with `pip install '.[postgres]'`).
