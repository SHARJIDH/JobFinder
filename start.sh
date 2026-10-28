#!/usr/bin/env bash
# AI Job Hunter — one command to set up and start everything on macOS or Linux.
#
#   ./start.sh                 set up what's missing, start on http://127.0.0.1:8000 and open the browser
#   ./start.sh --port 8080     use another port
#   ./start.sh --no-browser    don't open the browser
#   ./start.sh --setup-only    install/build everything, but don't start
#   ./start.sh --setup-ai      (re)connect Gemini: sign in to Google and pick a Google Cloud project
#
# Safe to re-run: it only installs or rebuilds what is missing or changed. Stop the app with Ctrl+C.
# Windows: use run.cmd instead (native PowerShell version: start.ps1).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PORT="${PORT:-8000}"
OPEN_BROWSER=1
SETUP_ONLY=0
SETUP_AI=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --no-browser) OPEN_BROWSER=0; shift ;;
    --setup-only) SETUP_ONLY=1; shift ;;
    --setup-ai) SETUP_AI=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
hash_of() { cat "$@" 2>/dev/null | { have shasum && shasum -a 256 || sha256sum; } | cut -c1-16; }
interactive() { [[ -t 0 && -t 1 ]]; }

OS="$(uname -s)"
ARCH="$(uname -m)"
[[ "$OS" == "Darwin" || "$OS" == "Linux" ]] || die "Unsupported OS '$OS'. On Windows, double-click run.cmd instead."
mkdir -p .tools/bin
export PATH="$ROOT/.tools/bin:$PATH"
[[ -d .tools/google-cloud-sdk/bin ]] && export PATH="$ROOT/.tools/google-cloud-sdk/bin:$PATH"
# Trust the operating system's certificate store (needed on networks with a company TLS proxy).
export UV_NATIVE_TLS=1 UV_SYSTEM_CERTS=1 NODE_USE_SYSTEM_CA=1

# ---------------------------------------------------------------- Python 3.11+
py_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; }
PYTHON=""
for c in python3.13 python3.12 python3.11 python3; do
  if have "$c" && py_ok "$c"; then PYTHON="$(command -v "$c")"; break; fi
done
if [[ -z "$PYTHON" ]]; then
  if [[ "$OS" == "Darwin" ]] && have brew; then
    say "Installing Python 3.12 with Homebrew"
    brew install python@3.12
    PYTHON="$(brew --prefix)/bin/python3.12"
  else
    die "Python 3.11+ is required. macOS: install Homebrew (https://brew.sh) then re-run. Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
  fi
fi

# ---------------------------------------------------------------- Node.js 20+
node_ok() { have node && [[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ]]; }
if ! node_ok; then
  if [[ "$OS" == "Darwin" ]] && have brew; then
    say "Installing Node.js with Homebrew"
    brew install node
  else
    die "Node.js 20+ is required. Install it from https://nodejs.org (LTS) or with nvm, then re-run."
  fi
fi

# ---------------------------------------------------------------- Tectonic (LaTeX compiler, optional)
if ! have tectonic; then
  say "Installing Tectonic (LaTeX compiler for resumes)"
  if [[ "$OS" == "Darwin" ]] && have brew; then
    brew install tectonic || warn "Tectonic install failed — LaTeX resumes disabled (plain PDFs still work)."
  elif [[ "$OS" == "Linux" ]]; then
    case "$ARCH" in x86_64) T=x86_64-unknown-linux-musl ;; aarch64|arm64) T=aarch64-unknown-linux-musl ;; *) T="" ;; esac
    if [[ -n "$T" ]] && curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-$T.tar.gz" \
         | tar -xz -C .tools/bin tectonic; then
      chmod +x .tools/bin/tectonic
    else
      warn "Could not install Tectonic — LaTeX resumes disabled (plain PDFs still work)."
    fi
  else
    warn "Install Tectonic (https://tectonic-typesetting.github.io) for LaTeX resumes; plain PDFs still work."
  fi
fi
if have tectonic; then
  CACHE_DIR="$HOME/.cache/Tectonic"; [[ "$OS" == "Darwin" ]] && CACHE_DIR="$HOME/Library/Caches/TectonicProject.Tectonic"
  if [[ ! -d "$CACHE_DIR" ]]; then
    say "Preparing the LaTeX package cache (one-time download, about a minute)"
    TMP="$(mktemp -d)"
    printf '%s\n' '\documentclass[10pt,a4paper]{article}' '\usepackage[margin=0.5in]{geometry}' '\usepackage[hidelinks]{hyperref}' \
      '\usepackage{enumitem}' '\usepackage{titlesec}' '\usepackage[T1]{fontenc}' '\usepackage{lmodern}' \
      '\begin{document}\textbf{Warm} \textit{cache}\end{document}' > "$TMP/w.tex"
    (cd "$TMP" && tectonic w.tex >/dev/null 2>&1) || warn "LaTeX cache warm-up failed; the first resume compile will be slower."
    rm -rf "$TMP"
  fi
fi

# ---------------------------------------------------------------- .env (secrets stay on this machine)
envget() { [[ -f .env ]] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//' || true; }
envset() {  # key value — replaces the line or appends it
  JH_K="$1" JH_V="$2" "$PYTHON" - <<'PY'
import os, re
k, v, p = os.environ["JH_K"], os.environ["JH_V"], ".env"
s = open(p, encoding="utf-8").read()
line = f"{k}={v}"
s = re.sub(rf"^{k}=.*$", lambda _m: line, s, flags=re.M) if re.search(rf"^{k}=", s, flags=re.M) else s.rstrip("\n") + f"\n{line}\n"
open(p, "w", encoding="utf-8").write(s)
os.chmod(p, 0o600)
PY
}
NEW_ENV=0
if [[ ! -f .env ]]; then
  say "Creating .env"
  cp .env.example .env
  NEW_ENV=1
  PASSWORD=""
  GENERATED=0
  if interactive; then
    read -r -s -p "Choose a dashboard password (Enter = generate one): " PASSWORD; echo
    if [[ -n "$PASSWORD" ]]; then
      read -r -s -p "Repeat it: " P2; echo
      [[ "$PASSWORD" == "$P2" ]] || { rm -f .env; die "Passwords did not match. Re-run ./start.sh"; }
    fi
  fi
  if [[ -z "$PASSWORD" ]]; then PASSWORD="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(12))')"; GENERATED=1; fi
  envset APP_PASSWORD "$PASSWORD"
  envset SESSION_SECRET "$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  envset LLM_PROVIDER none
  if [[ "$GENERATED" == 1 ]]; then
    say "Your dashboard password is: $PASSWORD   (saved as APP_PASSWORD in .env)"
  fi
fi
grep -q '^AUTO_REFRESH_HOURS=' .env || printf '\n# Refresh jobs when the app starts if the last daily run is older than this (hours, 0 = off)\nAUTO_REFRESH_HOURS=20\n' >> .env

# ---------------------------------------------------------------- backend
VENV="backend/.venv"
if [[ -d "$VENV" ]] && ! "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
  warn "Existing Python environment is broken (copied from another machine?) — recreating it"
  rm -rf "$VENV"
fi
if [[ ! -d "$VENV" ]]; then
  say "Creating Python environment"
  if have uv; then uv venv --python "$PYTHON" "$VENV" >/dev/null; else "$PYTHON" -m venv "$VENV"; fi
fi
DEPS_HASH="$(hash_of backend/pyproject.toml)"
if [[ "$(cat "$VENV/.jh-deps" 2>/dev/null)" != "$DEPS_HASH" ]]; then
  say "Installing backend dependencies"
  if have uv; then
    (cd backend && VIRTUAL_ENV="$ROOT/$VENV" uv pip install -q -e .)
  else
    "$VENV/bin/python" -m pip install -q --upgrade pip
    (cd backend && "$ROOT/$VENV/bin/python" -m pip install -q -e .)
  fi
  echo "$DEPS_HASH" > "$VENV/.jh-deps"
fi

# ---------------------------------------------------------------- Gemini (optional AI features)
adc_ok() { have gcloud && gcloud auth application-default print-access-token >/dev/null 2>&1; }

ensure_gcloud() {
  have gcloud && return 0
  say "Installing the Google Cloud CLI (needed to sign in to Google for Gemini)"
  if [[ "$OS" == "Darwin" ]] && have brew; then
    brew install --cask gcloud-cli || return 1
    hash -r
  else
    local os_name=linux arch_name
    [[ "$OS" == "Darwin" ]] && os_name=darwin
    case "$ARCH" in x86_64) arch_name=x86_64 ;; aarch64|arm64) arch_name=arm ;; *) return 1 ;; esac
    curl -fsSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-${os_name}-${arch_name}.tar.gz" \
      | tar -xz -C .tools || return 1
    export PATH="$ROOT/.tools/google-cloud-sdk/bin:$PATH" CLOUDSDK_PYTHON="$PYTHON"
  fi
  have gcloud
}

setup_gemini() {
  interactive || { warn "Gemini setup needs an interactive terminal. Run ./start.sh --setup-ai"; return 1; }
  ensure_gcloud || { warn "Could not install the Google Cloud CLI. Install it from https://cloud.google.com/sdk/docs/install, then run ./start.sh --setup-ai"; return 1; }

  if [[ -z "$(gcloud config get-value account 2>/dev/null)" ]] || ! adc_ok; then
    say "Sign in with your Google account — a browser window will open"
    gcloud auth login --update-adc || { warn "Google sign-in did not complete."; return 1; }
  else
    say "Signed in to Google as $(gcloud config get-value account 2>/dev/null)"
  fi

  local projects=() p current choice i=1
  while IFS= read -r p; do [[ -n "$p" ]] && projects+=("$p"); done < <(gcloud projects list --format='value(projectId)' --sort-by=projectId 2>/dev/null)
  if [[ ${#projects[@]} -eq 0 ]]; then
    warn "No Google Cloud projects found for this account."
    warn "Create one (with billing enabled) at https://console.cloud.google.com/projectcreate, then run ./start.sh --setup-ai"
    return 1
  fi
  current="$(envget GOOGLE_CLOUD_PROJECT)"
  [[ -z "$current" || "$current" == "your-gcp-project-id" ]] && current="$(gcloud config get-value project 2>/dev/null || true)"
  echo "Your Google Cloud projects:"
  for p in "${projects[@]}"; do echo "  $i) $p"; i=$((i + 1)); done
  read -r -p "Project for Gemini [number or ID${current:+, Enter = $current}]: " choice
  if [[ -z "$choice" ]]; then p="${current:-${projects[0]}}"
  elif [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#projects[@]} )); then p="${projects[$((choice - 1))]}"
  else p="$choice"; fi

  gcloud config set project "$p" >/dev/null 2>&1 || true
  gcloud auth application-default set-quota-project "$p" >/dev/null 2>&1 || true
  say "Enabling Vertex AI in $p"
  if ! gcloud services enable aiplatform.googleapis.com --project "$p"; then
    warn "Could not enable Vertex AI. Billing must be enabled: https://console.cloud.google.com/billing/linkedaccount?project=$p"
    return 1
  fi

  say "Testing Gemini"
  if (cd backend && JH_PROJECT="$p" JH_MODEL="$(envget GEMINI_MODEL_FAST)" .venv/bin/python - <<'PY'
import os
import app  # noqa: F401  (uses the OS certificate store)
from google import genai
client = genai.Client(vertexai=True, project=os.environ["JH_PROJECT"], location="global")
r = client.models.generate_content(model=os.environ.get("JH_MODEL") or "gemini-2.5-flash", contents="Reply with the word OK")
raise SystemExit(0 if r.text else 1)
PY
  ) >/dev/null 2>&1; then
    envset LLM_PROVIDER gemini
    envset GOOGLE_CLOUD_PROJECT "$p"
    envset GOOGLE_CLOUD_LOCATION global
    say "Gemini is on (project $p). Spending is capped by LLM_DAILY_BUDGET_USD in .env (default \$1/day)."
    return 0
  fi
  warn "Gemini did not respond in project $p (API just enabled? wait a minute and run ./start.sh --setup-ai)."
  return 1
}

LLM_NOW="$(envget LLM_PROVIDER)"
if [[ "$SETUP_AI" == 1 ]]; then
  setup_gemini || true
elif [[ "$NEW_ENV" == 1 ]] && interactive; then
  echo
  echo "AI features (match scoring, resume tailoring, web search) use Google Gemini, billed to your own"
  echo "Google Cloud project (about ₹400/month at most; capped at \$1/day by default)."
  read -r -p "Turn on AI now? You'll sign in with Google. [Y/n] " A
  [[ "$A" =~ ^[Nn] ]] || setup_gemini || true
elif [[ "$LLM_NOW" == "gemini" ]] && ! adc_ok; then
  warn "Gemini is enabled in .env but this laptop is not signed in to Google Cloud."
  if interactive; then setup_gemini || true; else warn "Run ./start.sh --setup-ai to sign in."; fi
fi
if [[ "$(envget LLM_PROVIDER)" == "gemini" ]]; then
  say "AI: Gemini (project $(envget GOOGLE_CLOUD_PROJECT))"
else
  warn "AI: off — the app works without it. Turn it on any time with ./start.sh --setup-ai"
fi

# ---------------------------------------------------------------- frontend
LOCK_HASH="$(hash_of frontend/package-lock.json)"
if [[ ! -d frontend/node_modules || "$(cat frontend/node_modules/.jh-deps 2>/dev/null)" != "$LOCK_HASH" ]]; then
  say "Installing frontend dependencies"
  (cd frontend && npm ci --no-audit --no-fund --loglevel=error)
  echo "$LOCK_HASH" > frontend/node_modules/.jh-deps
fi
SRC_PATHS=()
for p in frontend/app frontend/components frontend/lib frontend/public frontend/package-lock.json frontend/next.config.* \
         frontend/tsconfig.json frontend/postcss.config.*; do
  [[ -e "$p" ]] && SRC_PATHS+=("$p")
done
SRC_HASH="$(find "${SRC_PATHS[@]}" -type f | LC_ALL=C sort | xargs cat | { have shasum && shasum -a 256 || sha256sum; } | cut -c1-16)"
if [[ ! -f frontend/out/index.html || "$(cat frontend/out/.jh-src 2>/dev/null)" != "$SRC_HASH" ]]; then
  say "Building the dashboard"
  (cd frontend && npm run build --silent >/dev/null) || die "Dashboard build failed. Run 'cd frontend && npm run build' to see the error."
  echo "$SRC_HASH" > frontend/out/.jh-src
fi

# ---------------------------------------------------------------- database
say "Preparing the database"
(cd backend && .venv/bin/python -m app.cli migrate >/dev/null 2>&1) || die "Database migration failed. Run 'cd backend && .venv/bin/python -m app.cli migrate' to see the error."

[[ "$SETUP_ONLY" == 1 ]] && { say "Setup complete. Start with ./start.sh"; exit 0; }

# ---------------------------------------------------------------- run
URL="http://127.0.0.1:${PORT}"
if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
  say "AI Job Hunter is already running at $URL"
  [[ "$OPEN_BROWSER" == 1 ]] && { if have open; then open "$URL"; elif have xdg-open; then xdg-open "$URL" >/dev/null 2>&1; fi; }
  exit 0
fi
if have lsof && lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  die "Port $PORT is used by another program. Try: ./start.sh --port 8010"
fi

if [[ "$OPEN_BROWSER" == 1 ]]; then
  (
    for _ in $(seq 1 60); do
      if curl -fsS "$URL/api/health" >/dev/null 2>&1; then
        if have open; then open "$URL"; elif have xdg-open; then xdg-open "$URL" >/dev/null 2>&1; fi
        break
      fi
      sleep 1
    done
  ) &
fi
say "Starting AI Job Hunter at $URL  (Ctrl+C to stop; your data is in ./data)"
cd backend
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
