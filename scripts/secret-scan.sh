#!/usr/bin/env bash
# Secret scan with a pinned gitleaks binary (no GitHub Action license).
#
#   ./scripts/secret-scan.sh
#
# Used by CI/release. Does not print secret values (--redact).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GITLEAKS_VERSION="8.30.1"
# sha256 from https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1
GITLEAKS_SHA_LINUX_X64="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
GITLEAKS_SHA_DARWIN_X64="dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709"
GITLEAKS_SHA_DARWIN_ARM64="b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5"

fail() {
  echo "✗ $1" >&2
  exit 1
}

os="$(uname -s)"
arch="$(uname -m)"
asset=""
expect_sha=""
case "${os}:${arch}" in
  Linux:x86_64|Linux:amd64)
    asset="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
    expect_sha="$GITLEAKS_SHA_LINUX_X64"
    ;;
  Darwin:x86_64)
    asset="gitleaks_${GITLEAKS_VERSION}_darwin_x64.tar.gz"
    expect_sha="$GITLEAKS_SHA_DARWIN_X64"
    ;;
  Darwin:arm64)
    asset="gitleaks_${GITLEAKS_VERSION}_darwin_arm64.tar.gz"
    expect_sha="$GITLEAKS_SHA_DARWIN_ARM64"
    ;;
  *)
    fail "No pinned gitleaks binary for ${os}/${arch}"
    ;;
esac

bindir="$(mktemp -d)"
trap 'rm -rf "$bindir"' EXIT
archive="${bindir}/${asset}"
url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/${asset}"

echo "[secret-scan] gitleaks v${GITLEAKS_VERSION} (${asset})"
curl -fsSL -o "$archive" "$url"
got_sha=""
if command -v sha256sum >/dev/null 2>&1; then
  got_sha="$(sha256sum "$archive" | awk '{print $1}')"
else
  got_sha="$(shasum -a 256 "$archive" | awk '{print $1}')"
fi
if [ "$got_sha" != "$expect_sha" ]; then
  fail "gitleaks checksum mismatch (got ${got_sha}, expected ${expect_sha})"
fi
tar -xzf "$archive" -C "$bindir" gitleaks
chmod +x "${bindir}/gitleaks"

config=()
if [ -f "${ROOT}/.gitleaks.toml" ]; then
  config=(--config "${ROOT}/.gitleaks.toml")
fi

"${bindir}/gitleaks" detect \
  --source "$ROOT" \
  --redact \
  --no-banner \
  --exit-code 1 \
  "${config[@]}" \
  || fail "gitleaks found secrets"

echo "✓ Secret scan passed"
