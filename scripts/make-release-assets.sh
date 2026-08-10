#!/usr/bin/env bash
# Build the verified release assets the in-app updater requires:
#   paperlessagent-<version>.tar.gz
#   SHA256SUMS
#
# Usage (from a clean checkout of the release tag):
#   ./scripts/make-release-assets.sh v0.2.0
#
# Then create a GitHub release for that tag and upload both files from dist/.

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

tar -czf "$DIST/$ARCHIVE_NAME" -C "$STAGE" "$PREFIX"
(
  cd "$DIST"
  sha256sum "$ARCHIVE_NAME" > SHA256SUMS
)

echo "Release assets ready in $DIST:"
echo "  $ARCHIVE_NAME"
echo "  SHA256SUMS"
cat "$DIST/SHA256SUMS"
echo
echo "Upload both files to the GitHub release for $TAG."
echo "The updater refuses to install releases without these verified assets."
