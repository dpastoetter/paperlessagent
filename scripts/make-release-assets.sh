#!/usr/bin/env bash
# Build verified release assets for GitHub Releases / the in-app updater:
#   paperlessagent-<version>.tar.gz
#   paperlessagent_<version>_<arch>.deb   (when dpkg-deb is available)
#   SHA256SUMS
#
# Usage (from a clean checkout of the release tag):
#   ./scripts/make-release-assets.sh v0.2.0
#
# Optional:
#   SKIP_DEB=1  — skip .deb build even if dpkg-deb exists

set -euo pipefail

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "usage: $0 <tag>   e.g. $0 v0.2.0" >&2
  exit 1
fi

VERSION="${TAG#v}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
ARCHIVE_NAME="paperlessagent-${VERSION}.tar.gz"
STAGE="$DIST/stage"
SKIP_DEB="${SKIP_DEB:-0}"

rm -rf "$DIST"
mkdir -p "$STAGE"

# Pack the repository the same way GitHub source archives do: one root folder.
PREFIX="paperlessagent-${VERSION}"
mkdir -p "$STAGE/$PREFIX"

# Copy tracked files only (no .git, .venv, data, local .env).
if command -v git >/dev/null && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$STAGE/$PREFIX"
else
  echo "git checkout required to build a clean release archive" >&2
  exit 1
fi

# Never ship local secrets or runtime data even if somehow tracked.
rm -rf "$STAGE/$PREFIX/data" "$STAGE/$PREFIX/.venv" "$STAGE/$PREFIX/venv"
rm -f "$STAGE/$PREFIX/.env"

# Align archived pyproject version with the release tag.
if [ -f "$STAGE/$PREFIX/pyproject.toml" ]; then
  python3 - "$STAGE/$PREFIX/pyproject.toml" "$VERSION" <<'PY'
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

tar -czf "$DIST/$ARCHIVE_NAME" -C "$STAGE" "$PREFIX"

DEB_BUILT=0
if [ "$SKIP_DEB" != "1" ] && command -v dpkg-deb >/dev/null 2>&1; then
  "$ROOT/scripts/build-deb.sh" "$TAG"
  DEB_BUILT=1
elif [ "$SKIP_DEB" = "1" ]; then
  echo "Skipping .deb build (SKIP_DEB=1)."
else
  echo "Skipping .deb build (dpkg-deb not found). Install dpkg-dev to enable it."
fi

(
  cd "$DIST"
  # Checksum release artifacts only (not staging dirs).
  {
    sha256sum "$ARCHIVE_NAME"
    shopt -s nullglob
    for deb in ./*.deb; do
      sha256sum "${deb#./}"
    done
  } > SHA256SUMS
)

echo "Release assets ready in $DIST:"
echo "  $ARCHIVE_NAME"
if [ "$DEB_BUILT" = "1" ]; then
  ls -1 "$DIST"/*.deb 2>/dev/null | while read -r f; do echo "  $(basename "$f")"; done
fi
echo "  SHA256SUMS"
cat "$DIST/SHA256SUMS"
echo
echo "Upload these files to the GitHub release for $TAG."
echo "The in-app updater requires the .tar.gz + SHA256SUMS; the .deb is optional."
