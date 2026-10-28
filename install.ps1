# Install the WorkBuddy PPT skills into %USERPROFILE%\.workbuddy\skills and set up their runtime.
$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Src = Join-Path $RepoDir "skills"
$Dest = if ($env:WORKBUDDY_SKILLS_DIR) { $env:WORKBUDDY_SKILLS_DIR } else { Join-Path $env:USERPROFILE ".workbuddy\skills" }

$SkipDeps = $false
foreach ($arg in $args) {
    switch ($arg) {
        "--no-deps" { $SkipDeps = $true }
        { $_ -in @("-h", "--help") } {
            Write-Host "Usage: powershell -File install.ps1 [--no-deps]"
            Write-Host "  --no-deps   copy skills only; skip pip installs (useful for verification)"
            Write-Host "Env: WORKBUDDY_SKILLS_DIR  target skills directory (default %USERPROFILE%\.workbuddy\skills)"
            exit 0
        }
        default { Write-Error "Unknown argument: $arg"; exit 2 }
    }
}

function Say($msg) { Write-Host $msg }
function Fail($msg) { Write-Error $msg; exit 1 }

if (-not (Test-Path $Src)) { Fail "skills\ not found next to install.ps1" }

# --- locate a python interpreter -------------------------------------------------
$candidates = @(
    (Join-Path $env:USERPROFILE ".workbuddy\binaries\python\envs\default\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE ".workbuddy\binaries\python\envs\default\bin\python.exe")
)
$Py = $null
foreach ($c in $candidates) { if (Test-Path $c) { $Py = $c; break } }
if (-not $Py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source }
}
if (-not $Py) { Fail "no Python interpreter found; install Python 3.10+ or add it to PATH" }

Say "[1/4] Python: $Py"
& $Py -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if ($LASTEXITCODE -ne 0) { Fail "Python 3.10+ required" }

# --- copy skills -----------------------------------------------------------------
Say "[2/4] Installing skills into $Dest"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
foreach ($name in @("codex-ppt", "image-to-editable-ppt")) {
    $from = Join-Path $Src $name
    $to = Join-Path $Dest $name
    if (-not (Test-Path $from)) { Fail "missing $from" }
    if (Test-Path $to) { Remove-Item -Recurse -Force $to }
    Copy-Item -Recurse -Force $from $to
    Get-ChildItem -Path $to -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Say "      $name -> $to"
}

# --- python dependencies ---------------------------------------------------------
if ($SkipDeps) {
    Say "[3/4] Skipped dependency installation (--no-deps)"
} else {
    Say "[3/4] Installing Python dependencies"
    & $Py -m pip install --quiet --disable-pip-version-check -r (Join-Path $Dest "codex-ppt\requirements.txt")
    if ($LASTEXITCODE -ne 0) { Say "      warning: codex-ppt requirements failed; run pip install -r manually" }

    & $Py -m pip install --quiet --disable-pip-version-check --editable (Join-Path $Dest "image-to-editable-ppt\cli")
    if ($LASTEXITCODE -ne 0) { Say "      warning: editppt install failed; run pip install --editable <skill>\cli manually" }
}

# --- verify ----------------------------------------------------------------------
Say "[4/4] Verifying"
& $Py -c "import pptx, PIL; print('      python-pptx + Pillow ok')"
if ($LASTEXITCODE -ne 0) { Say "      warning: python-pptx/Pillow missing" }

& $Py -m editppt.cli doctor | Out-Null
if ($LASTEXITCODE -eq 0) { Say "      editppt doctor: ok" }
else { Say "      editppt doctor reported issues; run '$Py -m editppt.cli doctor' for details" }

Say ""
Say "Done. Restart the WorkBuddy session so the new skills are picked up."
Say "Next: give both skills an image backend (only one is needed):"
Say '  - use the WorkBuddy ImageGen tool when the session exposes it, or'
Say "  - log in with the Codex CLI for subscription-quota generation, or"
Say '  - write OPENAI_BASE_URL + OPENAI_API_KEY to %CODEX_PPT_HOME%\.env (default: %USERPROFILE%\.workbuddy\ppt-skill\.env)'
