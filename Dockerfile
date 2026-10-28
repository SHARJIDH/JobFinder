# syntax=docker/dockerfile:1
# One image for the Cloud Run service (API + static dashboard + scheduled daily run).
# SQLite lives on the instance disk and is replicated to Cloud Storage by Litestream.
# Browser automation is intentionally NOT included: it is a local-only, headed feature.

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_ENV=production DATA_DIR=/data PORT=8080
WORKDIR /srv/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
RUN pip install --upgrade pip && pip install '.[postgres]'
COPY config /srv/config
COPY --from=web /web/out /srv/frontend/out
RUN useradd --system --uid 10001 appuser && mkdir -p /data /var/lib/jobhunter && chown appuser /data /var/lib/jobhunter

# Tectonic (MIT) compiles LaTeX resumes. Its TeX bundle cache is pre-warmed at build
# time with the packages the built-in template uses, so the first compile is fast.
# Litestream (Apache-2.0) continuously replicates the SQLite database to Cloud Storage.
ARG TECTONIC_VERSION=0.17.0
ARG LITESTREAM_VERSION=0.3.13
ENV XDG_CACHE_HOME=/srv/cache
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates fontconfig \
 && curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin tectonic \
 && curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-amd64.tar.gz" \
    | tar -xz -C /usr/local/bin litestream \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /srv/cache /tmp/warm && cd /tmp/warm \
 && printf '%s\n' '\documentclass[10pt,a4paper]{article}' '\usepackage[margin=0.5in]{geometry}' \
    '\usepackage[hidelinks]{hyperref}' '\usepackage{enumitem}' '\usepackage{titlesec}' \
    '\usepackage[T1]{fontenc}' '\usepackage{lmodern}' '\begin{document}\textbf{Warm} \textit{cache}\end{document}' > w.tex \
 && tectonic w.tex && cd / && rm -rf /tmp/warm && chown -R appuser /srv/cache

COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
USER appuser
EXPOSE 8080
CMD ["sh", "/usr/local/bin/entrypoint.sh"]
