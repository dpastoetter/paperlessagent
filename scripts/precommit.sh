#!/usr/bin/env bash
# Pre-commit gate: run this before every commit (wired up as .git/hooks/pre-commit).
#
# Fast local checks first, then the same quality gate as GitHub Actions (./scripts/ci.sh)
# for format, lint, dependency integrity, types, JS syntax, and tests.
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

echo "[1/3] Python syntax"
python -m compileall -q deepcatalog app query_agent tests scripts \
  || fail "Python syntax errors"

echo "[2/3] Secret & data guard"
staged=$(git diff --cached --name-only --diff-filter=ACM || true)
if [ -n "$staged" ]; then
  # Files that must never be committed.
  blocked=$(echo "$staged" | grep -P '(^|/)\.env$|\.db$|^data/(?!inbox/\.gitkeep$)' || true)
  if [ -n "$blocked" ]; then
    fail "Refusing to commit sensitive/data files:
$blocked"
  fi
  # Obvious API keys / tokens in staged content.
  if git diff --cached -U0 -- $staged 2>/dev/null \
      | grep -E '^\+' \
      | grep -qE 'sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|ghp_[A-Za-z0-9]{30,}'; then
    fail "Staged changes appear to contain an API key or token"
  fi
fi
echo "  clean"

echo "[3/3] CI quality gate (ruff / pip check / mypy / JS / pytest)"
chmod +x scripts/ci.sh
./scripts/ci.sh || fail "CI quality gate failed"

echo "✓ All pre-commit checks passed"
