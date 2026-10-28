#!/usr/bin/env bash
# Install the WorkBuddy PPT skills into ~/.workbuddy/skills and set up their runtime.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$REPO_DIR/skills"
DEST="${WORKBUDDY_SKILLS_DIR:-$HOME/.workbuddy/skills}"

SKIP_DEPS=0
for arg in "$@"; do
  case "$arg" in
    --no-deps) SKIP_DEPS=1 ;;
    -h|--help)
      printf 'Usage: bash install.sh [--no-deps]\n'
      printf '  --no-deps   copy skills only; skip pip installs (useful for verification)\n'
      printf 'Env: WORKBUDDY_SKILLS_DIR  target skills directory (default ~/.workbuddy/skills)\n'
      exit 0
      ;;
    *) printf 'Unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

[ -d "$SRC" ] || fail "skills/ not found next to install.sh"

# --- locate a python interpreter -------------------------------------------------
PY=""
for candidate in \
  "$HOME/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
  "$HOME/.workbuddy/binaries/python/envs/default/bin/python" \
  "$(command -v python3 2>/dev/null || true)" \
  "$(command -v python 2>/dev/null || true)"
do
  if [ -n "$candidate" ] && { [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; }; then
    PY="$candidate"
    break
  fi
done
[ -n "$PY" ] || fail "no Python 3.10+ interpreter found; install Python or set PATH"

say "[1/5] Python: $PY"
"$PY" - <<'CHECK' || fail "Python 3.10+ required"
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"found {sys.version.split()[0]}")
print(f"      version {sys.version.split()[0]}")
CHECK

# --- copy skills -----------------------------------------------------------------
mkdir -p "$DEST"
say "[2/5] Installing skills into $DEST"
for name in codex-ppt image-to-editable-ppt; do
  src="$SRC/$name"
  dst="$DEST/$name"
  [ -d "$src" ] || fail "missing $src"
  case "$dst" in
    ""|"/"|"$DEST") fail "refusing to clean unexpected path: '$dst'" ;;
  esac
  if [ -e "$dst" ]; then
    rm -rf "$dst" || fail "cannot remove previous install at $dst"
  fi
  cp -R "$src" "$dst"
  find "$dst" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  say "      $name -> $dst"
done

# --- seed runtime config ---------------------------------------------------------
# Never overwrite an existing .env: it may hold credentials or hand-tuned proxy
# ports. Only seed when neither the new nor the legacy home has one.
RUNTIME_HOME="${CODEX_PPT_HOME:-$HOME/.workbuddy/ppt-skill}"
LEGACY_HOME="$HOME/.codex-ppt-skill"
if [ -f "$RUNTIME_HOME/.env" ] || [ -f "$LEGACY_HOME/.env" ]; then
  say "[3/5] Runtime config already exists; left untouched"
  if [ -f "$RUNTIME_HOME/.env" ]; then say "      $RUNTIME_HOME/.env"; else say "      $LEGACY_HOME/.env"; fi
else
  mkdir -p "$RUNTIME_HOME"
  cp "$DEST/codex-ppt/.env.example" "$RUNTIME_HOME/.env"
  chmod 600 "$RUNTIME_HOME/.env" 2>/dev/null || true
  say "[3/5] Seeded default runtime config (local Codex subscription quota)"
  say "      $RUNTIME_HOME/.env"
  say "      to use a relay API instead, fill CODEX_PPT_API_BASE_URL and OPENAI_API_KEY"
fi

# --- python dependencies ---------------------------------------------------------
if [ "$SKIP_DEPS" -eq 1 ]; then
  say "[4/5] Skipped dependency installation (--no-deps)"
else
  say "[4/5] Installing Python dependencies"
  "$PY" -m pip install --quiet --disable-pip-version-check -r "$DEST/codex-ppt/requirements.txt" \
    || say "      warning: codex-ppt requirements failed; run pip install -r manually"
  "$PY" -m pip install --quiet --disable-pip-version-check --editable "$DEST/image-to-editable-ppt/cli" \
    || say "      warning: editppt install failed; run pip install --editable <skill>/cli manually"
fi

# --- verify ----------------------------------------------------------------------
say "[5/5] Verifying"
"$PY" -c "import pptx, PIL; print('      python-pptx + Pillow ok')" || say "      warning: python-pptx/Pillow missing"
if "$PY" -m editppt.cli doctor >/dev/null 2>&1; then
  say "      editppt doctor: ok"
else
  say "      editppt doctor reported issues; run '$PY -m editppt.cli doctor' for details"
fi

say ""
say "Done. Restart the WorkBuddy session so the new skills are picked up."
say "Before generating images the skill now asks which quota to spend:"
say "  - local Codex subscription (zero API cost; requires 'codex login')"
say "  - relay / OpenAI-compatible API (needs CODEX_PPT_API_BASE_URL + OPENAI_API_KEY)"
say "Check the effective source any time:"
say "  python \"$DEST/codex-ppt/scripts/codex_ppt_runtime.py\" doctor"
