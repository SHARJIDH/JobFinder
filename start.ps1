# AI Job Hunter (JobFinder) - one command to set up and start everything on Windows 10/11.
#
#   Double-click run.cmd, or in PowerShell:   .\run.cmd
#   Options:  .\run.cmd -Port 8080   -NoBrowser   -SetupOnly   -SetupAI
#
# Safe to re-run: it only installs or rebuilds what is missing or changed. Stop the app with Ctrl+C.
# Installs missing tools with winget (Python 3.12, Node.js LTS, Google Cloud CLI) and downloads Tectonic.
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 reads scripts without a BOM as ANSI).
param(
  [int]$Port = 8000,
  [switch]$NoBrowser,
  [switch]$SetupOnly,
  [switch]$SetupAI
)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Root = $PSScriptRoot
Set-Location $Root
$Tools = [IO.Path]::Combine($Root, '.tools')
$ToolsBin = [IO.Path]::Combine($Tools, 'bin')
New-Item -ItemType Directory -Force -Path $ToolsBin | Out-Null

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "xx  $m" -ForegroundColor Red; exit 1 }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Is-Interactive { -not [Console]::IsInputRedirected }

function Update-PathEnv {
  $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $user = [Environment]::GetEnvironmentVariable('Path', 'User')
  $extra = @($ToolsBin)
  foreach ($d in @("$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin",
                   "${env:ProgramFiles(x86)}\Google\Cloud SDK\google-cloud-sdk\bin",
                   "$env:ProgramFiles\Google\Cloud SDK\google-cloud-sdk\bin",
                   "$env:ProgramFiles\nodejs")) {
    if ($d -and (Test-Path $d)) { $extra += $d }
  }
  $env:Path = (@($extra) + @($machine, $user) | Where-Object { $_ }) -join ';'
}
Update-PathEnv
# Use the operating system's certificate store (needed on networks with a company TLS proxy).
$env:UV_NATIVE_TLS = '1'; $env:UV_SYSTEM_CERTS = '1'; $env:NODE_USE_SYSTEM_CA = '1'

function Winget-Install($id, $what) {
  if (-not (Have 'winget')) {
    Fail "$what is required. Install it manually (winget is not available on this PC), then re-run."
  }
  Say "Installing $what with winget"
  & winget install -e --id $id --scope user --accept-package-agreements --accept-source-agreements --silent
  if ($LASTEXITCODE -ne 0) {
    # some packages don't support --scope user
    & winget install -e --id $id --accept-package-agreements --accept-source-agreements --silent
  }
  Update-PathEnv
}

function Get-Hash([string[]]$files) {
  $parts = foreach ($f in ($files | Sort-Object)) { (Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash }
  $sha = [Security.Cryptography.SHA256]::Create()
  $bytes = [Text.Encoding]::UTF8.GetBytes(($parts -join ''))
  (-join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') })).Substring(0, 16)
}

# ---------------------------------------------------------------- Python 3.11+
function Find-Python {
  $candidates = @(@('py', '-3.12'), @('py', '-3.13'), @('py', '-3.11'), @('python'), @('python3'))
  foreach ($c in $candidates) {
    if (-not (Have $c[0])) { continue }
    $cmdArgs = @()
    if ($c.Count -gt 1) { $cmdArgs = $c[1..($c.Count - 1)] }
    $exe = & $c[0] @cmdArgs -c "import sys; print(sys.executable) if sys.version_info >= (3, 11) else sys.exit(1)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path "$exe")) { return "$exe".Trim() }
  }
  return $null
}
$Python = Find-Python
if (-not $Python) {
  Winget-Install 'Python.Python.3.12' 'Python 3.12'
  $Python = Find-Python
  if (-not $Python) { Fail 'Python was installed but is not visible yet. Close this window, open a new one and run again.' }
}

# ---------------------------------------------------------------- Node.js 20+
function Node-Ok {
  if (-not (Have 'node')) { return $false }
  $major = & node -p "process.versions.node.split('.')[0]" 2>$null
  return ($LASTEXITCODE -eq 0 -and [int]$major -ge 20)
}
if (-not (Node-Ok)) {
  Winget-Install 'OpenJS.NodeJS.LTS' 'Node.js LTS'
  if (-not (Node-Ok)) { Fail 'Node.js was installed but is not visible yet. Close this window, open a new one and run again.' }
}

# ---------------------------------------------------------------- Tectonic (LaTeX compiler, optional)
if (-not (Have 'tectonic')) {
  Say 'Installing Tectonic (LaTeX compiler for resumes)'
  $zip = [IO.Path]::Combine($Tools, 'tectonic.zip')
  try {
    Invoke-WebRequest -UseBasicParsing -OutFile $zip `
      'https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-pc-windows-msvc.zip'
    Expand-Archive -Force -Path $zip -DestinationPath $ToolsBin
    Remove-Item $zip -Force
  } catch {
    Warn 'Could not install Tectonic - LaTeX resumes disabled (plain PDFs still work).'
  }
}
if (Have 'tectonic') {
  $cache = [IO.Path]::Combine($env:LOCALAPPDATA, 'TectonicProject')
  if (-not (Test-Path $cache)) {
    Say 'Preparing the LaTeX package cache (one-time download, about a minute)'
    $tmp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "jh-warm-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $lines = @('\documentclass[10pt,a4paper]{article}', '\usepackage[margin=0.5in]{geometry}', '\usepackage[hidelinks]{hyperref}',
               '\usepackage{enumitem}', '\usepackage{titlesec}', '\usepackage[T1]{fontenc}', '\usepackage{lmodern}',
               '\begin{document}\textbf{Warm} \textit{cache}\end{document}')
    [IO.File]::WriteAllLines([IO.Path]::Combine($tmp, 'w.tex'), $lines)
    Push-Location $tmp
    & tectonic w.tex *> $null
    if ($LASTEXITCODE -ne 0) { Warn 'LaTeX cache warm-up failed; the first resume compile will be slower.' }
    Pop-Location
    Remove-Item -Recurse -Force $tmp
  }
}

# ---------------------------------------------------------------- .env helpers (secrets stay on this PC)
$EnvFile = [IO.Path]::Combine($Root, '.env')
function Env-Get($key) {
  if (-not (Test-Path $EnvFile)) { return '' }
  $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match "^$key=" } | Select-Object -Last 1
  if (-not $line) { return '' }
  return (($line -replace "^$key=", '') -replace '\s+#.*$', '').Trim()
}
$EnvSetPy = @'
import os, re
k, v, p = os.environ["JH_K"], os.environ["JH_V"], ".env"
s = open(p, encoding="utf-8").read()
line = f"{k}={v}"
s = re.sub(rf"^{k}=.*$", lambda _m: line, s, flags=re.M) if re.search(rf"^{k}=", s, flags=re.M) else s.rstrip("\n") + f"\n{line}\n"
open(p, "w", encoding="utf-8", newline="\n").write(s)
'@
function Env-Set($key, $value) {
  $env:JH_K = $key; $env:JH_V = $value
  $EnvSetPy | & $Python -
  Remove-Item Env:JH_K, Env:JH_V
}
function New-Secret($n) { (& $Python -c "import secrets; print(secrets.token_urlsafe($n))").Trim() }

$NewEnv = $false
if (-not (Test-Path $EnvFile)) {
  Say 'Creating .env'
  Copy-Item ([IO.Path]::Combine($Root, '.env.example')) $EnvFile
  $NewEnv = $true
  $password = ''
  $generated = $false
  if (Is-Interactive) {
    $s1 = Read-Host 'Choose a dashboard password (Enter = generate one)' -AsSecureString
    $password = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s1))
    if ($password) {
      $s2 = Read-Host 'Repeat it' -AsSecureString
      $p2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s2))
      if ($password -ne $p2) { Remove-Item $EnvFile; Fail 'Passwords did not match. Run again.' }
    }
  }
  if (-not $password) { $password = New-Secret 12; $generated = $true }
  Env-Set 'APP_PASSWORD' $password
  Env-Set 'SESSION_SECRET' (New-Secret 48)
  Env-Set 'LLM_PROVIDER' 'none'
  if ($generated) { Say "Your dashboard password is: $password   (saved as APP_PASSWORD in .env)" }
}
if (-not (Env-Get 'AUTO_REFRESH_HOURS')) {
  Add-Content -Path $EnvFile -Encoding UTF8 -Value "`n# Refresh jobs when the app starts if the last daily run is older than this (hours, 0 = off)`nAUTO_REFRESH_HOURS=20"
}

# ---------------------------------------------------------------- backend
$Venv = [IO.Path]::Combine($Root, 'backend', '.venv')
$VenvPy = [IO.Path]::Combine($Venv, 'Scripts', 'python.exe')
if ((Test-Path $Venv) -and -not (Test-Path $VenvPy)) {
  Warn 'Existing Python environment is not a Windows one (copied from another machine?) - recreating it'
  Remove-Item -Recurse -Force $Venv
}
if ((Test-Path $VenvPy)) {
  & $VenvPy -c "import sys" 2>$null
  if ($LASTEXITCODE -ne 0) { Warn 'Existing Python environment is broken - recreating it'; Remove-Item -Recurse -Force $Venv }
}
if (-not (Test-Path $Venv)) {
  Say 'Creating Python environment'
  & $Python -m venv $Venv
  if ($LASTEXITCODE -ne 0) { Fail 'Could not create the Python environment.' }
}
$depsStamp = [IO.Path]::Combine($Venv, '.jh-deps')
$depsHash = Get-Hash @([IO.Path]::Combine($Root, 'backend', 'pyproject.toml'))
if (-not (Test-Path $depsStamp) -or (Get-Content $depsStamp) -ne $depsHash) {
  Say 'Installing backend dependencies (first time takes a few minutes)'
  Push-Location backend
  & $VenvPy -m pip install -q --upgrade pip
  & $VenvPy -m pip install -q -e .
  $ok = ($LASTEXITCODE -eq 0)
  Pop-Location
  if (-not $ok) { Fail 'Installing backend dependencies failed (see the messages above).' }
  Set-Content -Path $depsStamp -Value $depsHash
}

# ---------------------------------------------------------------- Gemini (optional AI features)
function Adc-Ok {
  if (-not (Have 'gcloud')) { return $false }
  & gcloud auth application-default print-access-token *> $null
  return ($LASTEXITCODE -eq 0)
}
function Ensure-Gcloud {
  if (Have 'gcloud') { return $true }
  Winget-Install 'Google.CloudSDK' 'Google Cloud CLI (needed to sign in to Google for Gemini)'
  return (Have 'gcloud')
}
$GeminiTestPy = @'
import os
import app  # noqa: F401  (uses the OS certificate store)
from google import genai
client = genai.Client(vertexai=True, project=os.environ["JH_PROJECT"], location="global")
r = client.models.generate_content(model=os.environ.get("JH_MODEL") or "gemini-2.5-flash", contents="Reply with the word OK")
raise SystemExit(0 if r.text else 1)
'@
function Setup-Gemini {
  if (-not (Is-Interactive)) { Warn 'Gemini setup needs an interactive window. Run: .\run.cmd -SetupAI'; return }
  if (-not (Ensure-Gcloud)) {
    Warn 'Could not install the Google Cloud CLI. Install it from https://cloud.google.com/sdk/docs/install, then run .\run.cmd -SetupAI'
    return
  }
  $account = (& gcloud config get-value account 2>$null | Out-String).Trim()
  if (-not $account -or -not (Adc-Ok)) {
    Say 'Sign in with your Google account - a browser window will open'
    & gcloud auth login --update-adc
    if ($LASTEXITCODE -ne 0) { Warn 'Google sign-in did not complete.'; return }
  } else {
    Say "Signed in to Google as $account"
  }
  $projects = @(& gcloud projects list --format='value(projectId)' --sort-by=projectId 2>$null | Where-Object { $_ })
  if ($projects.Count -eq 0) {
    Warn 'No Google Cloud projects found for this account.'
    Warn 'Create one (with billing enabled) at https://console.cloud.google.com/projectcreate, then run .\run.cmd -SetupAI'
    return
  }
  $current = Env-Get 'GOOGLE_CLOUD_PROJECT'
  if (-not $current -or $current -eq 'your-gcp-project-id') { $current = (& gcloud config get-value project 2>$null | Out-String).Trim() }
  Write-Host 'Your Google Cloud projects:'
  for ($i = 0; $i -lt $projects.Count; $i++) { Write-Host ("  {0}) {1}" -f ($i + 1), $projects[$i]) }
  $prompt = 'Project for Gemini [number or ID'
  if ($current) { $prompt += ", Enter = $current" }
  $choice = Read-Host ($prompt + ']')
  if (-not $choice) { if ($current) { $p = $current } else { $p = $projects[0] } }
  elseif ($choice -match '^\d+$' -and [int]$choice -ge 1 -and [int]$choice -le $projects.Count) { $p = $projects[[int]$choice - 1] }
  else { $p = $choice.Trim() }

  & gcloud config set project $p *> $null
  & gcloud auth application-default set-quota-project $p *> $null
  Say "Enabling Vertex AI in $p"
  & gcloud services enable aiplatform.googleapis.com --project $p
  if ($LASTEXITCODE -ne 0) {
    Warn "Could not enable Vertex AI. Billing must be enabled: https://console.cloud.google.com/billing/linkedaccount?project=$p"
    return
  }
  Say 'Testing Gemini'
  $env:JH_PROJECT = $p; $env:JH_MODEL = Env-Get 'GEMINI_MODEL_FAST'
  Push-Location backend
  $GeminiTestPy | & $VenvPy - *> $null
  $ok = ($LASTEXITCODE -eq 0)
  Pop-Location
  Remove-Item Env:JH_PROJECT, Env:JH_MODEL
  if ($ok) {
    Env-Set 'LLM_PROVIDER' 'gemini'
    Env-Set 'GOOGLE_CLOUD_PROJECT' $p
    Env-Set 'GOOGLE_CLOUD_LOCATION' 'global'
    Say "Gemini is on (project $p). Spending is capped by LLM_DAILY_BUDGET_USD in .env (default 1 USD/day)."
  } else {
    Warn "Gemini did not respond in project $p (API just enabled? wait a minute and run .\run.cmd -SetupAI)."
  }
}

if ($SetupAI) {
  Setup-Gemini
} elseif ($NewEnv -and (Is-Interactive)) {
  Write-Host ''
  Write-Host 'AI features (match scoring, resume tailoring, web search) use Google Gemini, billed to your own'
  Write-Host 'Google Cloud project (about Rs 400/month at most; capped at 1 USD/day by default).'
  $a = Read-Host "Turn on AI now? You'll sign in with Google. [Y/n]"
  if ($a -notmatch '^[Nn]') { Setup-Gemini }
} elseif ((Env-Get 'LLM_PROVIDER') -eq 'gemini' -and -not (Adc-Ok)) {
  Warn 'Gemini is enabled in .env but this PC is not signed in to Google Cloud.'
  if (Is-Interactive) { Setup-Gemini } else { Warn 'Run .\run.cmd -SetupAI to sign in.' }
}
if ((Env-Get 'LLM_PROVIDER') -eq 'gemini') {
  Say ("AI: Gemini (project {0})" -f (Env-Get 'GOOGLE_CLOUD_PROJECT'))
} else {
  Warn 'AI: off - the app works without it. Turn it on any time with: .\run.cmd -SetupAI'
}

# ---------------------------------------------------------------- frontend
$Fe = [IO.Path]::Combine($Root, 'frontend')
$lockHash = Get-Hash @([IO.Path]::Combine($Fe, 'package-lock.json'))
$nmStamp = [IO.Path]::Combine($Fe, 'node_modules', '.jh-deps')
if (-not (Test-Path $nmStamp) -or (Get-Content $nmStamp) -ne $lockHash) {
  Say 'Installing frontend dependencies'
  Push-Location $Fe
  & npm ci --no-audit --no-fund --loglevel=error
  $ok = ($LASTEXITCODE -eq 0)
  Pop-Location
  if (-not $ok) { Fail 'Installing frontend dependencies failed (see the messages above).' }
  Set-Content -Path $nmStamp -Value $lockHash
}
$srcFiles = @()
foreach ($d in @('app', 'components', 'lib', 'public')) {
  $dir = [IO.Path]::Combine($Fe, $d)
  if (Test-Path $dir) { $srcFiles += (Get-ChildItem -Recurse -File $dir | ForEach-Object { $_.FullName }) }
}
$srcFiles += (Get-ChildItem -File $Fe | Where-Object { $_.Name -match '^(package-lock\.json|tsconfig\.json|next\.config\..*|postcss\.config\..*)$' } | ForEach-Object { $_.FullName })
$srcHash = Get-Hash $srcFiles
$outIndex = [IO.Path]::Combine($Fe, 'out', 'index.html')
$outStamp = [IO.Path]::Combine($Fe, 'out', '.jh-src')
if (-not (Test-Path $outIndex) -or -not (Test-Path $outStamp) -or (Get-Content $outStamp) -ne $srcHash) {
  Say 'Building the dashboard'
  Push-Location $Fe
  & npm run build --silent *> $null
  $ok = ($LASTEXITCODE -eq 0)
  Pop-Location
  if (-not $ok) { Fail "Dashboard build failed. Run 'cd frontend; npm run build' to see the error." }
  Set-Content -Path $outStamp -Value $srcHash
}

# ---------------------------------------------------------------- database
Say 'Preparing the database'
Push-Location backend
& $VenvPy -m app.cli migrate *> $null
$ok = ($LASTEXITCODE -eq 0)
Pop-Location
if (-not $ok) { Fail "Database migration failed. Run 'cd backend; .venv\Scripts\python -m app.cli migrate' to see the error." }

if ($SetupOnly) { Say 'Setup complete. Start with .\run.cmd'; exit 0 }

# ---------------------------------------------------------------- run
$Url = "http://127.0.0.1:$Port"
function Healthy {
  try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$Url/api/health").StatusCode -eq 200 } catch { $false }
}
if (Healthy) {
  Say "AI Job Hunter is already running at $Url"
  if (-not $NoBrowser) { Start-Process $Url }
  exit 0
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Fail "Port $Port is used by another program. Try: .\run.cmd -Port 8010"
}
Say "Starting AI Job Hunter at $Url  (Ctrl+C to stop; your data is in .\data)"
$uvicorn = [IO.Path]::Combine($Venv, 'Scripts', 'uvicorn.exe')
$server = Start-Process -FilePath $uvicorn -ArgumentList @('app.main:app', '--host', '127.0.0.1', '--port', "$Port") `
  -WorkingDirectory ([IO.Path]::Combine($Root, 'backend')) -NoNewWindow -PassThru
try {
  if (-not $NoBrowser) {
    for ($i = 0; $i -lt 60; $i++) {
      if (Healthy) { Start-Process $Url; break }
      if ($server.HasExited) { break }
      Start-Sleep -Seconds 1
    }
  }
  $server.WaitForExit()
} finally {
  if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
