#!/usr/bin/env bash
# Pre-commit gate: run this before every commit (wired up as .git/hooks/pre-commit).
#
# Checks, fastest first so failures surface quickly:
#   1. Python syntax across all packages
#   2. JavaScript syntax for the web UI
#   3. Secret / data guard on staged files
#   4. Full offline test suite (no network, no LLM calls)
#
# Manual run:            ./scripts/precommit.sh
# Reinstall as a hook:   ln -sf ../../scripts/precommit.sh .git/hooks/pre-commit

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

fail() {
  echo "✗ $1" >&2
  exit 1
}

echo "[1/4] Python syntax"
python -m compileall -q paperless_agent app query_agent tests scripts \
  || fail "Python syntax errors"

echo "[2/4] JavaScript syntax"
if command -v node >/dev/null 2>&1; then
  for js in app/static/*.js; do
    node --check "$js" || fail "JS syntax error in $js"
  done
else
  echo "  node not found — skipping JS check"
fi

echo "[3/4] Secret & data guard"
staged=$(git diff --cached --name-only --diff-filter=ACM || true)
if [ -n "$staged" ]; then
  # Files that must never be committed.
  blocked=$(echo "$staged" | grep -P '(^|/)\.env$|\.db$|^data/(?!inbox/\.gitkeep$)' || true)
  if [ -n "$blocked" ]; then
    fail "Refusing to commit sensitive/data files:
$blocked"
  fi
  # Obvious API keys / tokens in staged content.
  if git diff --cached -U0 -- "$staged" 2>/dev/null \
      | grep -E '^\+' \
      | grep -qE 'sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{30,}'; then
    fail "Staged changes appear to contain an API key or token"
  fi
fi
echo "  clean"

echo "[4/4] Test suite"
python -m pytest tests/ -q || fail "Tests failed"

echo "✓ All pre-commit checks passed"
