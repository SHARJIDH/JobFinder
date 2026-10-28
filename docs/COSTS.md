# Cost estimate and usage controls

> All figures are **estimates** (USD→INR at ₹84). Your Google Cloud bill is authoritative. Promotional
> credits may not apply to every product (check *Billing → Credits* for the credit's scope and expiry).
> **Budget alerts notify you; they do not cap spending.**

## What runs where

| Operation | Where | Charges? |
|---|---|---|
| Job discovery (Greenhouse/Lever/Ashby/Workday HTTP) | Your machine or Cloud Run Job | No API fees; Cloud Run compute if hosted |
| Normalization, dedup, closure detection, deterministic scoring, validation, DOCX/PDF rendering | Local code | No |
| AI match assessment (`match`) | Vertex AI — `GEMINI_MODEL_FAST` (default `gemini-3.1-flash-lite`) | Yes, per token |
| Resume/cover-letter tailoring (`tailor`) | Vertex AI — `GEMINI_MODEL_QUALITY` (default `gemini-2.5-flash`, thinking disabled) | Yes, per token |
| Answer drafts (`answers`), profile extraction (`profile_draft`, opt-in) | Vertex AI — fast model | Yes, per token |
| LaTeX: apply tailoring to your template (`latex_apply`), one-page fit / "Ask Gemini" edits (`latex_revise`, includes PNG page screenshots) | Vertex AI — quality model | Yes, per token (each ~10–20k tokens; only when you have a `.tex` template or click the buttons) |
| LaTeX compilation (Tectonic) and page previews | Local code | No |
| Web search (`web_search`) for companies without a readable feed | Vertex AI — `GEMINI_MODEL_SEARCH` + Grounding with Google Search | Yes, per token **plus a per-grounded-request fee** (`PRICE_GROUNDED_PROMPT_USD`, default estimate $0.035) |
| Notifications | SMTP / Telegram Bot API | No |

## Measured usage (this environment, 2026-10-27)

| Task | Model | Calls | Input tok | Output tok | Est. cost |
|---|---|---|---|---|---|
| match | gemini-3.1-flash-lite | 8 | 11,885 | 2,772 | $0.0071 |
| tailor | gemini-2.5-flash | 1 | 1,953 | 840 | $0.0027 |

(Estimated with the deliberately conservative default prices in `.env.example`; verify current
Vertex AI pricing and update `PRICE_*` variables.)

## Monthly estimate (typical daily run)

Assumptions: ~25 AI match calls/day (only jobs with deterministic score ≥ 55, cached by content),
3 auto-tailored jobs/day, ~5 manual generations/answer drafts per day.

| Item | Estimate / month |
|---|---|
| Gemini (≈ 25×$0.0009 + 8×$0.003 ≈ $0.05/day) | **≈ $1.5 → ₹125** |
| Web search (3 companies/day × ≈$0.037, cached per day) | **≈ $3.3 → ₹280** |
| Cloud Run service (scale-to-zero, max 1 instance; ~10 min daily run + your sessions) | ₹0 (within free tier) |
| Cloud Scheduler (1 job) | ₹0 (3 free jobs per billing account) |
| Artifact Registry (~1 GB per image; delete old images occasionally) | ≈ ₹10–30 |
| Cloud Storage (YAML, PDFs, SQLite replica — a few MB) | ≈ ₹0–5 |
| Secret Manager (2 secrets) | ≈ ₹0 |
| **Total (deployed, 1–2 people)** | **≈ ₹400–450 / month, almost all Gemini** |

No database server is used: SQLite is replicated to Cloud Storage by Litestream (see DEPLOYMENT.md).
A managed Postgres (Cloud SQL ≈ ₹800–950/month extra) is only worth it for many users.

## Controls built into the app

| Control | Where |
|---|---|
| Daily model-call cap (`LLM_DAILY_CALL_LIMIT`, default 150) | `.env`; enforced before every call |
| Daily estimated spend cap (`LLM_DAILY_BUDGET_USD`, default $1.00) | `.env`; enforced before every call |
| Web search only for companies without a feed, max searches/page reads per run, results cached per day | `search.yaml → web_search` |
| AI scoring only above a deterministic threshold, max N per run | `search.yaml → limits` |
| Auto-tailoring limited to top Tier-A jobs, max N per run | `search.yaml → limits` |
| Response cache keyed by (task, prompt version, model, prompt, schema) | `llm_cache` table |
| Unchanged jobs are never re-scored (`score_input_hash`) | ranking |
| Cheap model for scoring, stronger model only for tailoring; thinking disabled on 2.5 models | `llm/service.py` |
| Usage log with tokens and estimated cost per call; Activity page shows totals | `llm_usage` table |
| Deterministic mode (`LLM_PROVIDER=none`) — zero model cost | `.env` |

## Budget alert (recommended, not a hard cap)

```bash
gcloud billing budgets create --billing-account=BILLING_ACCOUNT_ID --display-name=jobhunter \
  --budget-amount=1500INR --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
```
For an actual cap you would need a Pub/Sub-triggered function that disables billing — intentionally
not included because it can take your whole project offline.
