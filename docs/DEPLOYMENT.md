# Google Cloud deployment guide (sized for 1–2 people)

Everything is done by one idempotent script, `deploy/gcp/deploy.sh`. It prints commands only (dry run) unless
`CONFIRM=yes` is set. Re-running it is also how you deploy a new version.

## Architecture (≈ ₹0 infrastructure on the free tiers)

| Resource | Config | Why |
|---|---|---|
| Cloud Run service `jobhunter` (asia-south1) | 1 vCPU / 1 GiB, min 0, **max 1** instance, CPU always allocated while running, 30 min timeout | Dashboard + API + daily run; scales to zero when unused |
| SQLite + **Litestream** | DB on the instance disk, replicated to the bucket every second, restored on each start | No database server to pay for; a single writer is plenty for 1–2 people |
| Cloud Storage bucket (mounted at `/data`) | private, uniform access | profile.yaml, search.yaml, uploads, generated documents, DB replica |
| Cloud Scheduler `jobhunter-daily` | `30 7 * * *` Asia/Kolkata → `POST /api/internal/run-daily` | Sends a Google-signed OIDC token; the app verifies signature, audience and the scheduler account |
| Secret Manager | `APP_PASSWORD`, `SESSION_SECRET` (copied from your `.env`, never printed) | No secrets in images or env vars |
| Service account `jobhunter-run` | `aiplatform.user`, `storage.objectUser` on the bucket only, `secretAccessor` per secret | Least privilege; Gemini needs no API key |
| Service account `jobhunter-scheduler` | no roles | Only its identity is used to sign the scheduler's token |
| Compute default service account (Cloud Build) | read the `_cloudbuild` source bucket, write the image repo, write logs | New projects give it no permissions |

`.gcloudignore` keeps `.env`, `data/`, `node_modules` and virtualenvs out of the build upload.

Trade-offs: with one instance the dashboard and the daily run share CPU (fine for 1–2 people). During a new
deployment the old and new instance may overlap for a few seconds; avoid editing while deploying.
If you later need more users, set `DATABASE_URL` to Postgres (tested: all migrations and tests pass on
Postgres 15) and remove `LITESTREAM_REPLICA_URL`.

## Steps

```bash
PROJECT_ID=project-8ac0dc50-5eb6-4f4f-9b1 ./deploy/gcp/deploy.sh               # review (dry run)
PROJECT_ID=project-8ac0dc50-5eb6-4f4f-9b1 CONFIRM=yes ./deploy/gcp/deploy.sh   # deploy / update
```
Sign in at the printed URL with the `APP_PASSWORD` from your `.env`. To change it later, edit `.env` and run
with `ROTATE_SECRETS=yes CONFIRM=yes SKIP_BUILD=1 IMAGE=<current image>`.

Run the daily job now instead of waiting for 07:30:
```bash
gcloud scheduler jobs run jobhunter-daily --location asia-south1
```

**Corporate networks:** `gcloud` fails with `CERTIFICATE_VERIFY_FAILED` behind TLS-inspecting proxies.
Deploy from a personal network instead of disabling or overriding certificate checks.

## Things that stay local
Browser-assisted form filling needs a visible browser and is disabled in the container. Use it from your laptop.

## Rollback / teardown
```bash
gcloud scheduler jobs delete jobhunter-daily --location asia-south1
gcloud run services delete jobhunter --region asia-south1
gcloud storage rm -r gs://project-8ac0dc50-5eb6-4f4f-9b1-jobhunter-data   # deletes all cloud data
```
