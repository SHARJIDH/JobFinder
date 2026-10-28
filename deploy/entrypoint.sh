#!/bin/sh
# Container entrypoint. With LITESTREAM_REPLICA_URL set (Cloud Run), the SQLite database lives on the
# instance's local disk and is continuously replicated to Cloud Storage by Litestream; it is restored from
# there on every start. Without it (local Docker), the app just runs.
set -e
APP="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips=*"

if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
  DB="${SQLITE_PATH:-/var/lib/jobhunter/jobhunter.db}"
  mkdir -p "$(dirname "$DB")"
  litestream restore -if-db-not-exists -if-replica-exists -o "$DB" "$LITESTREAM_REPLICA_URL"
  python -m app.cli migrate   # creates the DB on first start (WAL mode) and applies migrations
  exec litestream replicate -exec "$APP" "$DB" "$LITESTREAM_REPLICA_URL"
fi
exec $APP
