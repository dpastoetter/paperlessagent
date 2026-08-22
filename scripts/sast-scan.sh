#!/usr/bin/env bash
# Static analysis with Semgrep community rules (Python + JavaScript).
#
#   ./scripts/sast-scan.sh
#
# Uses a throwaway venv so Semgrep cannot conflict with project packages.
# CodeQL remains the primary GitHub SAST; this adds pattern rules without a
# Semgrep Cloud token.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if command -v python >/dev/null 2>&1; then
  PY=python
else
  PY=python3
fi

fail() {
  echo "✗ $1" >&2
  exit 1
}

scan_venv="$(mktemp -d)"
trap 'rm -rf "$scan_venv"' EXIT

echo "[sast] semgrep (p/python + p/javascript, severity ERROR)"
"$PY" -m venv "$scan_venv"
"${scan_venv}/bin/pip" install -q -U pip
"${scan_venv}/bin/pip" install -q "semgrep>=1.90,<2"
"${scan_venv}/bin/semgrep" scan \
  --config p/python \
  --config p/javascript \
  --severity ERROR \
  --error \
  --metrics=off \
  --exclude .venv \
  --exclude node_modules \
  --exclude htmlcov \
  --exclude dist \
  --exclude tests \
  || fail "semgrep found ERROR-severity issues"

echo "✓ Semgrep scan passed"
