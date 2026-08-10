#!/usr/bin/env bash
# PaperlessAgent one-shot installer.
#
#   curl -fsSL https://raw.githubusercontent.com/dpastoetter/paperlessagent/main/scripts/install.sh | bash
#
# Optional env vars:
#   PAPERLESS_DIR   install location (default: ~/paperlessagent)
#   PAPERLESS_PORT  port printed in the run hint (default: 8080)

set -euo pipefail

REPO_SSH="git@github.com:dpastoetter/paperlessagent.git"
REPO_HTTPS="https://github.com/dpastoetter/paperlessagent.git"
INSTALL_DIR="${PAPERLESS_DIR:-$HOME/paperlessagent}"
PORT="${PAPERLESS_PORT:-8080}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die() { printf '  ✗ %s\n' "$*" >&2; exit 1; }

bold "PaperlessAgent installer"
echo "  → $INSTALL_DIR"
echo

# --- prerequisites -----------------------------------------------------------
command -v python3 >/dev/null || die "python3 is required (3.10+)"
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10+ required (found $PY_VER)"
ok "Python $PY_VER"

if ! command -v git >/dev/null; then
  die "git is required"
fi
ok "git"

if command -v pdftoppm >/dev/null; then
  ok "poppler (pdftoppm) — PDF OCR ready"
else
  warn "poppler not found — AI OCR for PDFs needs it"
  warn "  Fedora/RHEL:  sudo dnf install poppler-utils"
  warn "  Debian/Ubuntu: sudo apt install poppler-utils"
  warn "  macOS:         brew install poppler"
fi

# --- clone or update ---------------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
  bold "Updating existing install"
  git -C "$INSTALL_DIR" pull --ff-only || warn "git pull failed — continuing with local tree"
else
  if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR/.git" ]; then
    die "$INSTALL_DIR exists but is not a git checkout — move it aside or set PAPERLESS_DIR"
  fi
  bold "Cloning repository"
  if git clone --depth 1 "$REPO_HTTPS" "$INSTALL_DIR" 2>/dev/null; then
    ok "cloned via HTTPS"
  else
    git clone --depth 1 "$REPO_SSH" "$INSTALL_DIR" || die "git clone failed"
    ok "cloned via SSH"
  fi
fi

cd "$INSTALL_DIR"

# --- virtualenv + deps -------------------------------------------------------
bold "Creating virtualenv"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
ok "venv at $INSTALL_DIR/.venv"

bold "Installing Python packages"
python -m pip install -U pip >/dev/null
pip install -r requirements.txt
ok "dependencies installed"

# --- config ------------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  ok "created .env from .env.example"
else
  ok ".env already present — left untouched"
fi
mkdir -p data/inbox data/archive data/chroma
ok "data directories ready"

# --- done --------------------------------------------------------------------
echo
bold "Install complete"
cat <<EOF

  Start the app:

    cd $INSTALL_DIR
    source .venv/bin/activate
    uvicorn app.main:app --port $PORT

  Then open http://localhost:$PORT

  First-run tips:
    • Settings → Authentication — sign in with ChatGPT, or paste an API key
      (or set PAPERLESS_LLM_PROVIDER=ollama in .env for fully local models)
    • Settings → Filing & scanning — point the inbox at your scan folder
    • Drop a PDF in Inbox and click Process inbox

  Re-run this installer anytime to pull updates and refresh dependencies:
    curl -fsSL https://raw.githubusercontent.com/dpastoetter/paperlessagent/main/scripts/install.sh | bash

EOF
