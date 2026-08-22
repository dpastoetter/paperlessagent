#!/usr/bin/env bash
# Build a Linux x86_64 AppImage for PaperlessAgent.
#
# Usage (from a git checkout):
#   ./scripts/build-appimage.sh
#   ./scripts/build-appimage.sh v0.2.9
#   ./scripts/build-appimage.sh v0.2.9 HEAD
#
# Requires: linux x86_64, git, curl, tar, python3, pdftoppm, pdfinfo, patchelf, ldd.
# Downloads a pinned CPython and appimagetool (SHA-256 verified).
#
# Output:
#   dist/PaperlessAgent-<version>-x86_64.AppImage
#   dist/PaperlessAgent-<version>-x86_64.AppImage.sha256

set -euo pipefail

TAG="${1:-}"
REF_ARG="${2:-}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
APPDIR="$DIST/AppDir"
CACHE="$DIST/cache"
ARCH="x86_64"

# python-build-standalone (astral-sh) — install_only_stripped CPython 3.12.
PYTHON_RELEASE="20260814"
PYTHON_VERSION="3.12.14"
PYTHON_TARBALL="cpython-${PYTHON_VERSION}+${PYTHON_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}/${PYTHON_TARBALL}"
PYTHON_SHA256="5acfa3e9ba26b51ae161c83aff278da915b590d22373a424b2ba55b8afe91fcc"

# AppImage/appimagetool continuous build (pinned by digest).
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="a6d71e2b6cd66f8e8d16c37ad164658985e0cf5fcaa950c90a482890cb9d13e0"

die() {
  echo "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null || die "$1 is required to build the AppImage"
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

verify_sha256() {
  local path="$1"
  local expected="$2"
  local actual
  actual="$(sha256_file "$path")"
  if [ "$actual" != "$expected" ]; then
    die "SHA-256 mismatch for $(basename "$path") (expected ${expected}, got ${actual})"
  fi
}

download_verified() {
  local url="$1"
  local dest="$2"
  local sha="$3"
  if [ -f "$dest" ]; then
    if [ "$(sha256_file "$dest")" = "$sha" ]; then
      return 0
    fi
    rm -f "$dest"
  fi
  curl -fsSL --retry 3 --retry-delay 2 -o "$dest" "$url"
  verify_sha256 "$dest" "$sha"
}

pyproject_version() {
  python3 - "$ROOT/pyproject.toml" <<'PY'
from pathlib import Path
import re
import sys
text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
if not match:
    raise SystemExit("could not read version from pyproject.toml")
print(match.group(1))
PY
}

rewrite_pyproject_version() {
  python3 - "$1" "$2" <<'PY'
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
}

vendor_binary() {
  local src="$1"
  local dest_dir="$2"
  local native_dir="$3"
  local name
  name="$(basename "$src")"
  install -m 0755 "$src" "$dest_dir/$name"
  vendor_deps "$dest_dir/$name" "$native_dir"
  if command -v patchelf >/dev/null; then
    patchelf --set-rpath "\$ORIGIN/../lib/paperless-native" "$dest_dir/$name"
  fi
}

lib_excluded() {
  local base="$1"
  case "$base" in
    libc.so*|libm.so*|libpthread.so*|libdl.so*|librt.so*|libutil.so*|libresolv.so*|libnsl.so*|libcrypt.so*|ld-linux*|libgcc_s.so*|libstdc++.so*|libgtk-*|libwebkit*|libjavascriptcoregtk*|libgdk-*|libgdk_pixbuf*|libpango-*|libpangocairo*|libpangoft*|libsoup-*|libwayland-*|libX11.so*|libXext.so*|libXrender.so*|libXcursor.so*|libXrandr.so*|libXi.so*|libXfixes.so*|libxcb.so*|libgio-2*|libgobject-2*|libglib-2*|libgmodule-2*|libgthread-2*)
      return 0
      ;;
  esac
  return 1
}

vendor_deps() {
  local target="$1"
  local dest="$2"
  mkdir -p "$dest"
  local pending=("$target")
  local seen=""
  local item lib base
  while [ "${#pending[@]}" -gt 0 ]; do
    item="${pending[0]}"
    pending=("${pending[@]:1}")
    [ -f "$item" ] || continue
    while read -r lib; do
      [ -n "$lib" ] || continue
      [ -f "$lib" ] || continue
      base="$(basename "$lib")"
      if lib_excluded "$base"; then
        continue
      fi
      case " $seen " in
        *" $base "*) continue ;;
      esac
      seen+=" $base"
      cp -aL "$lib" "$dest/$base"
      pending+=("$dest/$base")
    done < <(ldd "$item" 2>/dev/null | awk '/=> \/|=> \.\// {print $3} /^\// {print $1}')
  done
  if command -v patchelf >/dev/null; then
    for lib in "$dest"/*; do
      [ -f "$lib" ] || continue
      patchelf --set-rpath "\$ORIGIN" "$lib" 2>/dev/null || true
    done
  fi
}

[ "$(uname -s)" = "Linux" ] || die "AppImage builds require Linux"
[ "$(uname -m)" = "x86_64" ] || die "AppImage builds currently support x86_64 only"

need_cmd git
need_cmd curl
need_cmd tar
need_cmd python3
need_cmd sha256sum
need_cmd ldd
need_cmd patchelf
need_cmd pdftoppm
need_cmd pdfinfo

if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "git checkout required to build a clean AppImage"
fi

if [ -z "$TAG" ]; then
  TAG="v$(pyproject_version)"
fi
VERSION="${TAG#v}"

if [ -n "$REF_ARG" ]; then
  REF="$REF_ARG"
elif git -C "$ROOT" rev-parse -q --verify "refs/tags/${TAG}^{commit}" >/dev/null; then
  REF="refs/tags/${TAG}"
else
  REF="HEAD"
fi

COMMIT="$(git -C "$ROOT" rev-parse "${REF}^{commit}")"
COMMIT_SHORT="$(git -C "$ROOT" rev-parse --short=12 "$COMMIT")"

rm -rf "$APPDIR" "$DIST/squashfs-root"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib/paperless-native" "$APPDIR/opt/paperlessagent" "$CACHE"

echo "Packing commit ${COMMIT_SHORT} as PaperlessAgent ${VERSION}"

git -C "$ROOT" archive --format=tar "$COMMIT" | tar -x -C "$APPDIR/opt/paperlessagent"

SRC="$APPDIR/opt/paperlessagent"
rm -rf \
  "$SRC/tests" \
  "$SRC/.github" \
  "$SRC/docs" \
  "$SRC/node_modules" \
  "$SRC/.venv" \
  "$SRC/data" \
  "$SRC/.env"

if [ ! -f "$SRC/pyproject.toml" ] || [ ! -f "$SRC/app/static/index.html" ]; then
  die "git archive is missing required project files"
fi
if [ ! -f "$SRC/packaging/linux/AppRun" ]; then
  die "packaging/linux/AppRun missing from commit ${COMMIT_SHORT} — commit packaging assets first"
fi
if [ ! -f "$SRC/packaging/linux/paperlessagent.png" ]; then
  die "packaging/linux/paperlessagent.png missing from commit ${COMMIT_SHORT}"
fi

rewrite_pyproject_version "$SRC/pyproject.toml" "$VERSION"

install -m 0755 "$SRC/packaging/linux/AppRun" "$APPDIR/AppRun"
install -m 0644 "$SRC/packaging/linux/paperlessagent.desktop" "$APPDIR/paperlessagent.desktop"
install -m 0644 "$SRC/packaging/linux/paperlessagent.svg" "$APPDIR/paperlessagent.svg"
install -m 0644 "$SRC/packaging/linux/paperlessagent.png" "$APPDIR/paperlessagent.png"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
install -m 0644 "$SRC/packaging/linux/paperlessagent.svg" \
  "$APPDIR/usr/share/icons/hicolor/scalable/apps/paperlessagent.svg"
install -m 0644 "$SRC/packaging/linux/paperlessagent.png" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps/paperlessagent.png"

cat > "$APPDIR/usr/bin/paperlessagent" <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
SELF="$(readlink -f "$0")"
BIN="$(dirname "$SELF")"
export APPDIR="$(cd "$BIN/../.." && pwd)"
exec "$APPDIR/AppRun" "$@"
WRAP
chmod +x "$APPDIR/usr/bin/paperlessagent"

download_verified "$PYTHON_URL" "$CACHE/$PYTHON_TARBALL" "$PYTHON_SHA256"
rm -rf "$CACHE/python"
mkdir -p "$CACHE/python-extract"
tar -xzf "$CACHE/$PYTHON_TARBALL" -C "$CACHE/python-extract"
if [ -d "$CACHE/python-extract/python" ]; then
  cp -a "$CACHE/python-extract/python/." "$APPDIR/usr/"
else
  die "python-build-standalone tarball did not contain a python/ directory"
fi
rm -rf "$CACHE/python-extract"

PYTHON="$APPDIR/usr/bin/python3"
[ -x "$PYTHON" ] || die "bundled python3 missing"
"$PYTHON" -m ensurepip --upgrade >/dev/null
"$PYTHON" -m pip install -U pip
"$PYTHON" -m pip install --no-warn-script-location \
  -c "$SRC/constraints.txt" \
  "$SRC[desktop]"

vendor_binary "$(command -v pdftoppm)" "$APPDIR/usr/bin" "$APPDIR/usr/lib/paperless-native"
vendor_binary "$(command -v pdfinfo)" "$APPDIR/usr/bin" "$APPDIR/usr/lib/paperless-native"

download_verified "$APPIMAGETOOL_URL" "$CACHE/appimagetool-x86_64.AppImage" "$APPIMAGETOOL_SHA256"
chmod +x "$CACHE/appimagetool-x86_64.AppImage"

APPIMAGE_NAME="PaperlessAgent-${VERSION}-x86_64.AppImage"
rm -f "$DIST/$APPIMAGE_NAME"

# appimagetool is itself an AppImage; extract-and-run so CI (no FUSE) can pack.
ARCH="$ARCH" VERSION="$VERSION" APPIMAGE_EXTRACT_AND_RUN=1 \
  "$CACHE/appimagetool-x86_64.AppImage" "$APPDIR" "$DIST/$APPIMAGE_NAME"
chmod +x "$DIST/$APPIMAGE_NAME"

# Smoke: extract without FUSE and import the FastAPI app + run --help.
rm -rf "$DIST/squashfs-root"
(
  cd "$DIST"
  "./$APPIMAGE_NAME" --appimage-extract >/dev/null
)
SMOKE="$DIST/squashfs-root"
[ -x "$SMOKE/usr/bin/python3" ] || die "extracted AppImage is missing python3"
[ -x "$SMOKE/usr/bin/pdftoppm" ] || die "extracted AppImage is missing pdftoppm"
PAPERLESS_PROJECT_ROOT="$SMOKE/opt/paperlessagent" \
  PYTHONPATH="$SMOKE/opt/paperlessagent" \
  "$SMOKE/usr/bin/python3" -c "from app.main import app; print(app.version)"
"$SMOKE/usr/bin/pdftoppm" -v >/dev/null
"$SMOKE/AppRun" --help >/dev/null
rm -rf "$DIST/squashfs-root"

(
  cd "$DIST"
  sha256sum "$APPIMAGE_NAME" | tee "${APPIMAGE_NAME}.sha256"
)

echo "AppImage ready:"
echo "  $DIST/$APPIMAGE_NAME"
echo "  commit ${COMMIT_SHORT} (${COMMIT})"
echo "  version ${VERSION}"
