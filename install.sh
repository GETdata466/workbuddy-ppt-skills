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

say "[1/4] Python: $PY"
"$PY" - <<'CHECK' || fail "Python 3.10+ required"
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"found {sys.version.split()[0]}")
print(f"      version {sys.version.split()[0]}")
CHECK

# --- copy skills -----------------------------------------------------------------
mkdir -p "$DEST"
say "[2/4] Installing skills into $DEST"
for name in codex-ppt image-to-editable-ppt; do
  src="$SRC/$name"
  dst="$DEST/$name"
  [ -d "$src" ] || fail "missing $src"
  rm -rf "$dst"
  cp -R "$src" "$dst"
  find "$dst" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  say "      $name -> $dst"
done

# --- python dependencies ---------------------------------------------------------
if [ "$SKIP_DEPS" -eq 1 ]; then
  say "[3/4] Skipped dependency installation (--no-deps)"
else
  say "[3/4] Installing Python dependencies"
  "$PY" -m pip install --quiet --disable-pip-version-check -r "$DEST/codex-ppt/requirements.txt" \
    || say "      warning: codex-ppt requirements failed; run pip install -r manually"
  "$PY" -m pip install --quiet --disable-pip-version-check --editable "$DEST/image-to-editable-ppt/cli" \
    || say "      warning: editppt install failed; run pip install --editable <skill>/cli manually"
fi

# --- verify ----------------------------------------------------------------------
say "[4/4] Verifying"
"$PY" -c "import pptx, PIL; print('      python-pptx + Pillow ok')" || say "      warning: python-pptx/Pillow missing"
if "$PY" -m editppt.cli doctor >/dev/null 2>&1; then
  say "      editppt doctor: ok"
else
  say "      editppt doctor reported issues; run '$PY -m editppt.cli doctor' for details"
fi

say ""
say "Done. Restart the WorkBuddy session so the new skills are picked up."
say "Next: give both skills an image backend (only one is needed):"
say "  - use the WorkBuddy ImageGen tool when the session exposes it, or"
say "  - log in with the Codex CLI for subscription-quota generation, or"
say "  - write OPENAI_BASE_URL + OPENAI_API_KEY to \${CODEX_PPT_HOME:-~/.workbuddy/ppt-skill}/.env"
