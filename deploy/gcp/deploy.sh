#!/usr/bin/env bash
# Google Cloud deployment for AI Job Hunter, sized for 1–2 people (≈ ₹0 infrastructure on the free tiers):
#   * 1 Cloud Run service  — dashboard + API + scheduled daily run; scale-to-zero, max 1 instance
#   * SQLite on the instance disk, continuously replicated to Cloud Storage by Litestream (restored on start)
#   * 1 private Cloud Storage bucket mounted at /data (profile/search YAML, uploads, generated documents)
#   * 1 Cloud Scheduler job → POST /api/internal/run-daily with a Google-signed OIDC token (verified by the app)
#   * Secret Manager for APP_PASSWORD / SESSION_SECRET; dedicated least-privilege service accounts
#
# SAFETY: this script only PRINTS the commands unless you run it with CONFIRM=yes.
# Secrets are read from your local .env and piped straight into Secret Manager — never printed.
# Re-running is safe (existing resources are skipped) and is also how you deploy a new version.
set -euo pipefail
cd "$(dirname "$0")/../.."

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"              # Mumbai; Gemini itself is called via the 'global' endpoint
SERVICE="${SERVICE:-jobhunter}"
REPO="${REPO:-jobhunter}"
BUCKET="${BUCKET:-${PROJECT_ID}-jobhunter-data}"
SA_NAME="jobhunter-run"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="jobhunter-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/app:$(date +%Y%m%d-%H%M%S)}"
SCHEDULE="${SCHEDULE:-30 7 * * *}"           # 07:30 every day
TZ_NAME="${TZ_NAME:-Asia/Kolkata}"
ENV_FILE="${ENV_FILE:-.env}"

run() {
  echo "+ $*"
  if [[ "${CONFIRM:-}" == "yes" ]]; then "$@"; fi
}
try() { run "$@" || echo "  (skipped: probably already exists)"; }
envval() { [[ -f "$ENV_FILE" ]] && grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }
put_secret() {  # name value — value is never echoed
  local name="jobhunter-$1" value="$2"
  echo "+ secret $name (value hidden)"
  [[ "${CONFIRM:-}" == "yes" ]] || return 0
  [[ -n "$value" ]] || { echo "  $1 is empty in $ENV_FILE — aborting"; exit 1; }
  if gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    [[ "${ROTATE_SECRETS:-}" == "yes" ]] && printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project "$PROJECT_ID" >/dev/null
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --replication-policy automatic --project "$PROJECT_ID" >/dev/null
  fi
  gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:${SA}" \
    --role roles/secretmanager.secretAccessor --project "$PROJECT_ID" >/dev/null
}

[[ "${CONFIRM:-}" == "yes" ]] || echo "### DRY RUN — printing commands only. Re-run with CONFIRM=yes to execute. ###"

# 1. APIs (no cost by themselves)
run gcloud services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com aiplatform.googleapis.com --project "$PROJECT_ID"

# 2. Service accounts: runtime may call Vertex AI and use its own bucket/secrets; scheduler only signs tokens
try gcloud iam service-accounts create "$SA_NAME" --display-name "AI Job Hunter runtime" --project "$PROJECT_ID"
try gcloud iam service-accounts create jobhunter-scheduler --display-name "AI Job Hunter scheduler" --project "$PROJECT_ID"
run gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${SA}" --role roles/aiplatform.user \
  --condition=None --quiet >/dev/null

# 3. Private bucket (data files + Litestream replica). Your local profile/search settings are copied once.
try gcloud storage buckets create "gs://${BUCKET}" --location "$REGION" --uniform-bucket-level-access \
  --public-access-prevention --project "$PROJECT_ID"
run gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --member "serviceAccount:${SA}" \
  --role roles/storage.objectUser >/dev/null
for f in data/profile.yaml data/search.yaml; do
  [[ -f "$f" ]] && try gcloud storage cp --no-clobber "$f" "gs://${BUCKET}/$(basename "$f")"
done

# 4. Secrets from .env (never printed). Set ROTATE_SECRETS=yes to push new values.
put_secret app_password "$(envval APP_PASSWORD)"
put_secret session_secret "$(envval SESSION_SECRET)"

# 5. Build image with Cloud Build (free tier: 2,500 build-minutes/month). .gcloudignore keeps .env and data/ out.
try gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" --project "$PROJECT_ID"
# New projects build as the Compute default service account with no permissions; grant only what a build needs.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
try gcloud storage buckets create "gs://${PROJECT_ID}_cloudbuild" --location US --uniform-bucket-level-access --project "$PROJECT_ID"
run gcloud storage buckets add-iam-policy-binding "gs://${PROJECT_ID}_cloudbuild" --member "serviceAccount:${BUILD_SA}" \
  --role roles/storage.objectViewer >/dev/null
run gcloud artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" --member "serviceAccount:${BUILD_SA}" \
  --role roles/artifactregistry.writer --project "$PROJECT_ID" >/dev/null
run gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:${BUILD_SA}" --role roles/logging.logWriter \
  --condition=None --quiet >/dev/null
[[ -n "${SKIP_BUILD:-}" ]] || run gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID" .

# 6. Cloud Run service: one instance at most (SQLite has a single writer), scale to zero when idle.
#    CPU stays allocated while the instance lives so background runs from the dashboard finish.
ENV="APP_ENV=production,COOKIE_SECURE=true,DATA_DIR=/data,DATABASE_URL=sqlite:////var/lib/jobhunter/jobhunter.db"
ENV+=",LITESTREAM_REPLICA_URL=gcs://${BUCKET}/litestream/jobhunter.db,CRON_INVOKER_EMAIL=${SCHED_SA}"
ENV+=",LLM_PROVIDER=gemini,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,LLM_DAILY_CALL_LIMIT=150,LLM_DAILY_BUDGET_USD=1.00"
run gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --service-account "$SA" \
  --execution-environment gen2 --min-instances 0 --max-instances 1 --cpu 1 --memory 1Gi --concurrency 20 \
  --timeout 1800 --no-cpu-throttling \
  --add-volume name=data,type=cloud-storage,bucket="${BUCKET}" --add-volume-mount volume=data,mount-path=/data \
  --set-env-vars "$ENV" \
  --set-secrets "APP_PASSWORD=jobhunter-app_password:latest,SESSION_SECRET=jobhunter-session_secret:latest" \
  --allow-unauthenticated --project "$PROJECT_ID"
URL="https://SERVICE_URL"
[[ "${CONFIRM:-}" == "yes" ]] && URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')"
run gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "PUBLIC_BASE_URL=${URL}" --project "$PROJECT_ID"
echo "# The dashboard is protected by the app password (HTTPS-only cookie, rate-limited login)."

# 7. Daily run: Cloud Scheduler calls the service with an OIDC token for the scheduler account.
CRON_URL="${URL}/api/internal/run-daily"
if [[ "${CONFIRM:-}" == "yes" ]] && gcloud scheduler jobs describe "${SERVICE}-daily" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  ACTION=update
else
  ACTION=create
fi
run gcloud scheduler jobs "$ACTION" http "${SERVICE}-daily" --location "$REGION" --schedule "$SCHEDULE" --time-zone "$TZ_NAME" \
  --uri "$CRON_URL" --http-method POST --oidc-service-account-email "$SCHED_SA" --oidc-token-audience "$CRON_URL" \
  --attempt-deadline 1800s --project "$PROJECT_ID"

echo "# Dashboard: ${URL}"
echo "# Budget alert (alerts only — NOT a hard spending cap):"
echo "#   gcloud billing budgets create --billing-account BILLING_ACCOUNT_ID --display-name jobhunter \\"
echo "#     --budget-amount 1000INR --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0"
