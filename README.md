# JobFinder — AI Job Hunter

A private, cost-conscious job-search assistant: it discovers fresh engineering roles at your target
companies from permitted public sources, ranks them against your **verified** profile with an
explainable score, tailors an ATS-friendly resume and cover letter per job with Gemini (Vertex AI),
prepares an application workspace, and tracks everything — while **you** approve and submit every
application yourself.

- Repository audit and foundation choice: [docs/AUDIT.md](docs/AUDIT.md)
- Architecture, database schema, migrations: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Cost estimate and usage controls: [docs/COSTS.md](docs/COSTS.md)
- Google Cloud deployment (optional, not used — runs locally with `./start.sh`): [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- What works, known limitations, future work: [docs/STATUS.md](docs/STATUS.md)

## Where jobs come from

| Company | Source | Notes |
|---|---|---|
| Google | Google Careers results (newest first) | first page per search term only — robots.txt disallows pagination |
| Microsoft, Qualcomm | Eightfold `/api/pcsx` | explicitly allowed in their robots.txt |
| Amazon | amazon.jobs search JSON | |
| Atlassian | careers listings JSON | full list → closures detected |
| Oracle | Oracle Recruiting Cloud REST | |
| Intuit | sitemap + schema.org JobPosting | |
| Adobe, NVIDIA, Salesforce, Cisco | Workday career-site JSON | |
| Uber, Walmart Global Tech, ServiceNow | Gemini + Google Search grounding on official domains | pages block automated reading → stored as *unverified leads* |
| Databricks, Stripe, MongoDB, Okta, … (22 more) | Greenhouse / Lever / Ashby public APIs | |
| Anything else | “Add job manually” (with “Fetch details” from the official URL) | |

Add or remove companies in *Search settings*. Every request respects robots.txt (RFC 9309) and rate limits; 401/403
responses, CAPTCHAs and bot checks are never bypassed.

## Ground rules the code enforces

- Nothing about you is invented. Generated text may only use facts from `data/profile.yaml` and your
  uploaded resume; rewritten bullets that add numbers, tools or claims are rejected and the original is kept.
- Salaries and posting dates are never guessed; unknown values are shown as unverified.
- External content (job descriptions) is treated as data, delimited, and scanned for injected instructions.
- No CAPTCHA/anti-bot bypass, no login scraping; 401/403 and robots.txt disallows stop the source.
- No application is marked *Submitted* without your explicit approval of the exact documents/answers **and**
  evidence of submission. The browser helper never clicks Submit.

## Local setup — one command

**Windows 10/11:** double-click `run.cmd` (or run `.\run.cmd` in PowerShell / Command Prompt).
**macOS / Linux:** `./start.sh`

That's it. On a new laptop it installs/builds whatever is missing (Python 3.12, Node.js LTS and the Google Cloud
CLI via winget on Windows or Homebrew on macOS; the Tectonic LaTeX compiler is downloaded), creates `.env`
(asks you for a dashboard password or generates one), offers to turn on Gemini, migrates the
database, starts the app on http://127.0.0.1:8000 and opens your browser. Re-running only redoes what changed.
Stop with Ctrl+C.

| | Windows (`run.cmd`) | macOS / Linux (`start.sh`) |
|---|---|---|
| Other port | `-Port 8080` | `--port 8080` |
| Don't open the browser | `-NoBrowser` | `--no-browser` |
| Install/build only | `-SetupOnly` | `--setup-only` |
| Sign in to Google / change project for Gemini | `-SetupAI` | `--setup-ai` |

If Windows just installed Python or Node and says it can't find them yet, close the window and run `run.cmd` again.

- **Jobs refresh when you start the app** if the last daily run is older than `AUTO_REFRESH_HOURS` (default 20)
  — it runs in the background while you browse.
- **Gemini** (optional, guided): on first run, or any time with `./start.sh --setup-ai`, the script installs the
  Google Cloud CLI if needed, opens the Google sign-in page in your browser, lists your Google Cloud projects to
  pick from, enables Vertex AI and checks that Gemini answers. If `.env` says Gemini but the laptop isn't signed
  in (e.g. you copied `.env`), it asks you to sign in. Without Gemini the app runs in deterministic mode (no AI cost).
  You need a Google Cloud project with billing enabled.
- **Moving to another laptop:** copy the project folder. To keep your profile, resumes and jobs, also copy
  `data/` (and `.env` for the same password). Don't copy `backend/.venv` or `frontend/node_modules`; they are
  recreated.
- Works behind company TLS proxies: Python, uv and Node use the operating system's certificate store.

Manual setup (if you prefer):
```bash
cp .env.example .env              # set APP_PASSWORD and SESSION_SECRET (long random values)
cd backend && uv venv .venv && uv pip install -e '.[dev]' && .venv/bin/python -m app.cli migrate
cd ../frontend && npm ci && npm run build
cd ../backend && .venv/bin/uvicorn app.main:app --port 8000
```

Frontend development with hot reload: run the backend on :8000, then
`cd frontend && NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev` and open http://localhost:3000.

### First steps in the dashboard
1. **Profile & resume** → upload one or more resumes (PDF/DOCX, e.g. one per specialisation) and add
   *Additional context* notes about projects and achievements in your own words. Click *Build draft* to merge
   everything into one profile (review, then save), and confirm the skills you actually have.
   Fill `application_facts` (notice period, work authorisation…) — blank facts are always asked, never guessed.
   Tailoring then picks the most relevant projects and bullets from the combined profile for each job.
2. **Search settings** → adjust companies, roles, cities, the ₹30 LPA target, weights and limits.
3. **Overview** → *Run full daily workflow* (discover → dedupe → verify → rank → tailor top jobs).
4. Open a job → review the score breakdown → *Tailor resume* opens the **LaTeX editor** (see below) →
   *Prepare application* → verify answers, tick the checklist,
   **Approve** → submit on the official site → *Record confirmed submission* with the confirmation text.

### LaTeX resumes (Overleaf-style editor)
Install the LaTeX compiler once: `brew install tectonic` (macOS) — the Docker image already contains it.
The first compile downloads its TeX package bundle (~1 min); later compiles take a second or two.

- Upload your own resume as a **`.tex`** file in *Profile & resume* to use it as your template (single-file;
  `\input`, shell-escape and file-writing commands are refused). Without one, a built-in ATS-friendly template is used.
- *Tailor resume* on a job → Gemini applies the tailored plan to your template, the server compiles it, and the
  editor opens: LaTeX source on the left, rendered pages on the right. Edit and press **⌘/Ctrl+S** to recompile.
- **Fit to one page**: the built-in template tightens layout, then drops the least relevant lines. For your own
  template, Gemini receives **screenshots of the rendered pages** plus the source and may only shorten, remove or
  re-space (up to 2 rounds; kept only if the page count actually drops).
- **Ask Gemini** box: free-form edits ("put the RAG project first"). Edits that add numbers or skills not in your
  profile/resumes/notes are rejected; LaTeX that fails to compile gets one automatic repair attempt, otherwise
  nothing changes. Your own manual edits are allowed but flagged if they contain unverified facts, and they
  revoke any existing approval.
- **Open in Overleaf** sends the source to overleaf.com as a new project (asks first; not synced back).
- Set `RESUME_FORMAT=classic` to go back to ReportLab PDFs.

### Daily automation locally
```bash
# crontab -e   (07:30 every day)
30 7 * * * cd /path/to/repo/backend && .venv/bin/python -m app.cli run-daily >> ../data/cron.log 2>&1
```

### Optional: assisted form filling (local only)
```bash
cd backend && uv pip install -e '.[browser]' && .venv/bin/playwright install chromium
# .env: BROWSER_AUTOMATION_ENABLED=true
```
Supported: Greenhouse, Lever and Ashby hosted forms. It opens a visible browser, fills verified fields,
uploads the tailored resume, stops at CAPTCHAs and leaves Submit to you.

### Notifications
Email: set `SMTP_HOST/PORT/USERNAME/PASSWORD` (e.g. a Gmail App Password), `NOTIFY_EMAIL_TO`.
Telegram: create a bot with @BotFather, set `TELEGRAM_BOT_TOKEN` and your `TELEGRAM_CHAT_ID`.
Preview the digest under *Activity & costs*.

## CLI

```bash
python -m app.cli migrate                 # apply Alembic migrations
python -m app.cli discover [--company X]  # fetch + dedupe + closure detection
python -m app.cli rank [--no-ai] [--force]
python -m app.cli run-daily [--no-notify]
python -m app.cli notify [--dry-run]
```

## Tests

```bash
cd backend && .venv/bin/python -m pytest          # 92 tests: parsing, sources (mocked HTTP), robots.txt, JSON-LD, web search, dedup, expiry,
                                                  # ranking, LLM budget/cache/failures, truthful tailoring, LaTeX safety/fit/editor,
                                                  # approval/submission guard, uploads, notifications, browser fill
cd frontend && npm run lint && npx tsc --noEmit && npm run build
```
Browser-fill tests need `playwright install chromium`; they are skipped otherwise.

## Configuration files

| File | Purpose |
|---|---|
| `.env` (from `.env.example`) | Secrets, model names, daily call/spend caps, notification channels |
| `data/profile.yaml` (seeded from `config/profile.example.yaml`) | Your verified facts — the only source for generated content |
| `data/search.yaml` (seeded from `config/search.example.yaml`) | Companies & sources, roles, locations, compensation, weights, tiers, limits |

`data/` and `.env` are git-ignored. Delete personal data any time from *Profile → Privacy & data deletion*.

## Licence
MIT. Greenhouse/Lever connector logic adapted from [Insideofcub/ai-job-hunter](https://github.com/Insideofcub/ai-job-hunter) (MIT).
