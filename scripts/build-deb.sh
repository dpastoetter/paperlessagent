#!/usr/bin/env bash
# Build an amd64 .deb that installs PaperlessAgent under /opt/paperlessagent.
#
#   ./scripts/build-deb.sh v0.1.3
#
# Output: dist/paperlessagent_<version>_amd64.deb

set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "usage: $0 <tag>   e.g. $0 v0.1.3" >&2
  exit 1
fi

VERSION="${TAG#v}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
PKG_NAME="paperlessagent"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
DEB_NAME="${PKG_NAME}_${VERSION}_${ARCH}.deb"
OPT_ROOT="/opt/paperlessagent"
STAGE="$DIST/deb-root"
BUILD_APP="$STAGE$OPT_ROOT"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v dpkg-deb >/dev/null || { echo "dpkg-deb is required (apt install dpkg-dev)" >&2; exit 1; }

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10+ required to build the package venv" >&2; exit 1; }

rm -rf "$STAGE"
mkdir -p "$BUILD_APP" "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/lib/systemd/user"

# --- application files -------------------------------------------------------
if command -v git >/dev/null && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$BUILD_APP"
else
  echo "git checkout required to build a clean package" >&2
  exit 1
fi
rm -rf "$BUILD_APP/data" "$BUILD_APP/.venv" "$BUILD_APP/venv" "$BUILD_APP/dist"
rm -f "$BUILD_APP/.env"

# Rewrite pyproject version so the installed tree matches the release tag.
if [ -f "$BUILD_APP/pyproject.toml" ]; then
  python3 - "$BUILD_APP/pyproject.toml" "$VERSION" <<'PY'
from pathlib import Path
import re
import sys
path = Path(sys.argv[1])
version = sys.argv[2]
text = path.read_text(encoding="utf-8")
text, n = re.subn(
    r'^version\s*=\s*"[^"]*"',
    f'version = "{version}"',
    text,
    count=1,
    flags=re.MULTILINE,
)
if n != 1:
    raise SystemExit("could not rewrite version in pyproject.toml")
path.write_text(text, encoding="utf-8")
PY
fi

# --- virtualenv at the final install path (then rewrite shebangs) ------------
echo "Creating virtualenv and installing dependencies…"
python3 -m venv "$BUILD_APP/.venv"
# shellcheck disable=SC1091
source "$BUILD_APP/.venv/bin/activate"
python -m pip install -U pip >/dev/null
# Production install — skip pytest from requirements.txt.
grep -vE '^(pytest|[[:space:]]*#|$)' "$BUILD_APP/requirements.txt" \
  | python -m pip install -r /dev/stdin
deactivate

# Make the venv relocatable to /opt/paperlessagent/.venv after dpkg install.
BUILD_VENV="$BUILD_APP/.venv"
FINAL_VENV="$OPT_ROOT/.venv"
if [ -f "$BUILD_VENV/pyvenv.cfg" ]; then
  sed -i "s|$BUILD_VENV|$FINAL_VENV|g" "$BUILD_VENV/pyvenv.cfg"
fi
# Rewrite shebangs that point at the staging venv path.
while IFS= read -r -d '' file; do
  if head -n 1 "$file" | grep -q "^#!$BUILD_VENV"; then
    sed -i "1s|$BUILD_VENV|$FINAL_VENV|" "$file"
  fi
done < <(find "$BUILD_VENV/bin" -type f -print0)

# --- launcher ----------------------------------------------------------------
cat > "$STAGE/usr/bin/paperlessagent" <<'EOF'
#!/bin/sh
# PaperlessAgent launcher (Debian package).
set -eu

APP="/opt/paperlessagent"
VENV="$APP/.venv"
PORT="${PAPERLESS_PORT:-8080}"
HOST="${PAPERLESS_HOST:-127.0.0.1}"
export DATA_DIR="${DATA_DIR:-$HOME/.local/share/paperlessagent}"

if [ ! -x "$VENV/bin/uvicorn" ]; then
  echo "paperlessagent: missing $VENV (reinstall the package)" >&2
  exit 1
fi

mkdir -p "$DATA_DIR/inbox" "$DATA_DIR/archive" "$DATA_DIR/chroma"
if [ ! -f "$APP/.env" ] && [ -f "$APP/.env.example" ]; then
  # Prefer a per-user env under DATA_DIR; fall back to copying example once.
  if [ ! -f "$DATA_DIR/.env" ]; then
    cp "$APP/.env.example" "$DATA_DIR/.env"
  fi
fi
if [ -f "$DATA_DIR/.env" ]; then
  # Export KEY=VALUE lines for the process (ignore comments / blanks).
  set -a
  # shellcheck disable=SC1090
  . "$DATA_DIR/.env"
  set +a
fi

cmd="${1:-start}"
case "$cmd" in
  start)
    cd "$APP"
    exec "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
    ;;
  stop)
    if command -v systemctl >/dev/null 2>&1; then
      systemctl --user stop paperlessagent.service
    else
      echo "paperlessagent: systemctl not available; stop the uvicorn process manually" >&2
      exit 1
    fi
    ;;
  status)
    if command -v systemctl >/dev/null 2>&1; then
      systemctl --user status paperlessagent.service --no-pager
    else
      echo "paperlessagent: installed at $APP (DATA_DIR=$DATA_DIR)"
    fi
    ;;
  uninstall)
    cat <<HINT
To remove the package:
  sudo apt remove paperlessagent
  # or: sudo dpkg -r paperlessagent

User data is kept at: $DATA_DIR
HINT
    ;;
  *)
    cat <<USAGE
usage: paperlessagent [start|stop|status|uninstall]

  start      run the web UI on ${HOST}:${PORT} (default)
  stop       stop the systemd --user service
  status     show systemd --user status
  uninstall  print package removal hints

Environment:
  DATA_DIR        default: ~/.local/share/paperlessagent
  PAPERLESS_PORT  default: 8080
  PAPERLESS_HOST  default: 127.0.0.1
USAGE
    exit 1
    ;;
esac
EOF
chmod 755 "$STAGE/usr/bin/paperlessagent"

# --- systemd user unit -------------------------------------------------------
cat > "$STAGE/usr/lib/systemd/user/paperlessagent.service" <<EOF
[Unit]
Description=PaperlessAgent local document agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/paperlessagent start
Restart=on-failure
RestartSec=3
Environment=DATA_DIR=%h/.local/share/paperlessagent
# Keep the API on loopback unless the user overrides PAPERLESS_HOST.
Environment=PAPERLESS_HOST=127.0.0.1
Environment=PAPERLESS_PORT=8080

[Install]
WantedBy=default.target
EOF

# --- control files -----------------------------------------------------------
# Installed size in KiB (approximate).
INSTALLED_SIZE="$(du -sk "$STAGE" | awk '{print $1}')"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.10), poppler-utils
Maintainer: Dominik Pastoetter <266259608+dpastoetter@users.noreply.github.com>
Installed-Size: ${INSTALLED_SIZE}
Homepage: https://github.com/dpastoetter/paperlessagent
Description: Local-first document agent with human-in-the-loop filing
 PaperlessAgent ingests scanned PDFs and photos, recovers text with AI
 vision OCR, extracts metadata, and files documents locally with optional
 RAG search. Supports OpenAI, Gemini, or fully local Ollama.
EOF

cat > "$STAGE/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e
if command -v systemctl >/dev/null 2>&1; then
  # Reload unit definitions for users that already have a session bus.
  systemctl --user daemon-reload >/dev/null 2>&1 || true
fi
cat <<MSG

PaperlessAgent ${VERSION} installed to /opt/paperlessagent

  Start now:     paperlessagent start
  As a service:  systemctl --user enable --now paperlessagent
  Open:          http://127.0.0.1:8080

Data directory defaults to ~/.local/share/paperlessagent

MSG
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user stop paperlessagent.service >/dev/null 2>&1 || true
  systemctl --user disable paperlessagent.service >/dev/null 2>&1 || true
fi
EOF
chmod 755 "$STAGE/DEBIAN/prerm"

mkdir -p "$DIST"
dpkg-deb --root-owner-group --build "$STAGE" "$DIST/$DEB_NAME"

echo "Debian package ready:"
echo "  $DIST/$DEB_NAME"
ls -lh "$DIST/$DEB_NAME"
