#!/usr/bin/env bash
# Reproducible quality gate used by GitHub Actions and locally.
#
#   ./scripts/ci.sh
#
# Steps: ruff format → ruff lint → pip check → mypy → JS syntax → Vitest → pytest

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

fail() {
  echo "✗ $1" >&2
  exit 1
}

if command -v python >/dev/null 2>&1; then
  PY=python
else
  PY=python3
fi

echo "[1/7] Ruff format"
"$PY" -m ruff format --check paperless_agent app query_agent tests \
  || fail "Formatting drift — run: $PY -m ruff format paperless_agent app query_agent tests"

echo "[2/7] Ruff lint"
"$PY" -m ruff check paperless_agent app query_agent tests \
  || fail "Ruff lint failed"

echo "[3/7] pip check"
"$PY" -m pip check || fail "pip check reported broken dependencies"

echo "[4/7] mypy"
"$PY" -m mypy || fail "mypy failed"

echo "[5/7] JavaScript syntax"
if command -v node >/dev/null 2>&1; then
  for js in app/static/*.js; do
    node --check "$js" || fail "JS syntax error in $js"
  done
else
  echo "  node not found — skipping JS check"
fi

echo "[6/7] Frontend unit tests (Vitest)"
if command -v npm >/dev/null 2>&1; then
  if [ ! -d node_modules/vitest ]; then
    if [ -f package-lock.json ]; then
      npm ci --no-fund --no-audit || fail "npm ci failed"
    else
      npm install --no-fund --no-audit || fail "npm install failed"
    fi
  fi
  npm test || fail "Frontend unit tests failed"
else
  echo "  npm not found — skipping Vitest"
fi

echo "[7/7] pytest + coverage"
"$PY" -m pytest tests/ -q || fail "Tests failed (or coverage below floor)"

echo "✓ CI quality gate passed"
