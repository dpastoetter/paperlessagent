"""Self-update: check GitHub for a newer release and install it in place."""

from __future__ import annotations

import hashlib
import hmac
import io
import logging
import os
import re
import shutil
import sys
import tarfile
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx

from paperless_agent import config

logger = logging.getLogger(__name__)

_DEFAULT_UPDATE_REPO = "dpastoetter/paperlessagent"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA256_LINE_RE = re.compile(
    r"^\s*([A-Fa-f0-9]{64})\s+\*?(.+?)\s*$"
)
_DIGEST_RE = re.compile(r"^sha256:([A-Fa-f0-9]{64})$", re.IGNORECASE)
_SUMS_NAMES = frozenset({"SHA256SUMS", "SHA256SUMS.txt", "checksums.txt"})


def _resolve_update_repo() -> str:
    raw = os.getenv("PAPERLESS_UPDATE_REPO", _DEFAULT_UPDATE_REPO).strip()
    if not _REPO_RE.fullmatch(raw):
        logger.warning(
            "Ignoring invalid PAPERLESS_UPDATE_REPO=%r; using %s",
            raw,
            _DEFAULT_UPDATE_REPO,
        )
        return _DEFAULT_UPDATE_REPO
    return raw


UPDATE_REPO = _resolve_update_repo()
GITHUB_API = "https://api.github.com"

# Never overwritten by an update: user data, credentials, environments.
# Matched case-insensitively so a tarball cannot sneak past with Data/ or .ENV.
PROTECTED_TOP_LEVEL = {"data", ".env", ".venv", "venv", ".git", "node_modules"}


def get_current_version() -> str:
    """Read the installed version from pyproject.toml."""
    pyproject = Path(config.PROJECT_ROOT) / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def parse_version(value: str) -> tuple[int, ...]:
    """'v1.2.3' → (1, 2, 3); non-numeric parts are ignored."""
    numbers = re.findall(r"\d+", value or "")
    return tuple(int(n) for n in numbers) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse GNU sha256sum output into `{filename: hex digest}`."""
    mapping: dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SHA256_LINE_RE.match(line)
        if not match:
            continue
        digest, filename = match.group(1).lower(), match.group(2).strip()
        # Also index by basename so "dist/foo.tar.gz" matches "foo.tar.gz".
        mapping[filename] = digest
        mapping[Path(filename).name] = digest
    return mapping


def _asset_digest(asset: dict[str, Any]) -> str | None:
    raw = (asset.get("digest") or "").strip()
    if not raw:
        return None
    match = _DIGEST_RE.match(raw)
    return match.group(1).lower() if match else None


def _pick_archive_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    archives = [
        asset
        for asset in assets
        if isinstance(asset.get("name"), str)
        and asset["name"] not in _SUMS_NAMES
        and asset["name"].lower().endswith((".tar.gz", ".tgz"))
    ]
    if not archives:
        return None
    preferred = [
        asset
        for asset in archives
        if asset["name"].lower().startswith("paperlessagent-")
    ]
    return preferred[0] if preferred else archives[0]


def _resolve_commit_sha(client: httpx.Client, tag: str) -> str | None:
    if not tag:
        return None
    resp = client.get(f"{GITHUB_API}/repos/{UPDATE_REPO}/commits/{tag}")
    if resp.status_code != 200:
        return None
    sha = (resp.json() or {}).get("sha")
    return sha if isinstance(sha, str) and sha else None


def _select_verified_artifact(
    release: dict[str, Any],
    *,
    sums_text: str | None,
) -> dict[str, Any] | None:
    """
    Choose a downloadable archive that has an expected SHA-256.

    Prefers an uploaded `.tar.gz` release asset. Uses the asset's GitHub
    `digest` when present, otherwise a `SHA256SUMS` asset entry.
    """
    assets = [
        asset
        for asset in (release.get("assets") or [])
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    ]
    sums = parse_sha256sums(sums_text or "")
    archive = _pick_archive_asset(assets)
    if archive is None:
        return None

    expected = _asset_digest(archive) or sums.get(archive["name"])
    if not expected:
        return None
    url = archive.get("browser_download_url") or archive.get("url")
    if not isinstance(url, str) or not url:
        return None
    return {
        "filename": archive["name"],
        "download_url": url,
        "expected_sha256": expected,
        "source": "release-asset",
    }


def _fetch_latest_release() -> dict[str, Any] | None:
    """Latest GitHub release with verification metadata (no unverified tag fallback for install)."""
    headers = {"Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=15, follow_redirects=True, headers=headers) as client:
        resp = client.get(f"{GITHUB_API}/repos/{UPDATE_REPO}/releases/latest")
        if resp.status_code == 404:
            # No releases published — surface tags for "what's newest" only.
            resp = client.get(
                f"{GITHUB_API}/repos/{UPDATE_REPO}/tags", params={"per_page": 1}
            )
            resp.raise_for_status()
            tags = resp.json()
            if not tags:
                return None
            tag = tags[0].get("name") or ""
            commit_sha = _resolve_commit_sha(client, tag)
            return {
                "tag": tag,
                "name": tag,
                "notes": "",
                "published_at": None,
                "html_url": f"https://github.com/{UPDATE_REPO}/releases",
                "tarball_url": f"{GITHUB_API}/repos/{UPDATE_REPO}/tarball/{tag}",
                "commit_sha": commit_sha,
                "assets": [],
                "verifiable": False,
                "artifact": None,
                "verification_error": (
                    "No GitHub release with a SHA-256-verified archive asset. "
                    "Tag-only installs are disabled."
                ),
            }
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name") or ""
        assets = data.get("assets") or []
        if not isinstance(assets, list):
            assets = []

        sums_text: str | None = None
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if asset.get("name") not in _SUMS_NAMES:
                continue
            sums_url = asset.get("browser_download_url") or asset.get("url")
            if not isinstance(sums_url, str):
                continue
            sums_headers = dict(headers)
            # Asset API URLs need the octet-stream accept to get bytes.
            if sums_url.startswith(GITHUB_API):
                sums_headers["Accept"] = "application/octet-stream"
            sums_resp = client.get(sums_url, headers=sums_headers)
            if sums_resp.is_success:
                sums_text = sums_resp.text
                break

        release = {
            "tag": tag,
            "name": data.get("name") or tag,
            "notes": (data.get("body") or "")[:2000],
            "published_at": data.get("published_at"),
            "html_url": data.get("html_url"),
            "tarball_url": data.get("tarball_url"),
            "commit_sha": _resolve_commit_sha(client, tag),
            "assets": assets,
        }
        artifact = _select_verified_artifact(release, sums_text=sums_text)
        release["artifact"] = artifact
        release["verifiable"] = artifact is not None
        release["verification_error"] = (
            None
            if artifact is not None
            else (
                "Latest release is missing a .tar.gz asset with a SHA-256 digest "
                "(GitHub asset digest or SHA256SUMS). Refusing unverified installs."
            )
        )
        return release


def check_for_update() -> dict[str, Any]:
    """Compare the installed version against the latest GitHub release."""
    current = get_current_version()
    base = {
        "status": "success",
        "repo": UPDATE_REPO,
        "current_version": current,
        "update_available": False,
        "verifiable": False,
    }
    try:
        latest = _fetch_latest_release()
    except httpx.HTTPError as exc:
        return {
            "status": "error",
            "repo": UPDATE_REPO,
            "current_version": current,
            "error": f"Could not reach GitHub: {exc}",
        }
    if latest is None:
        return {**base, "message": "No releases or tags published on GitHub yet."}

    artifact = latest.get("artifact") or {}
    return {
        **base,
        "latest_version": latest["tag"].lstrip("v"),
        "latest_tag": latest["tag"],
        "release_name": latest["name"],
        "notes": latest["notes"],
        "published_at": latest["published_at"],
        "html_url": latest["html_url"],
        "tarball_url": latest.get("tarball_url"),
        "commit_sha": latest.get("commit_sha"),
        "update_available": is_newer(latest["tag"], current),
        "verifiable": bool(latest.get("verifiable")),
        "verification_error": latest.get("verification_error"),
        "artifact_name": artifact.get("filename"),
        "expected_sha256": artifact.get("expected_sha256"),
        "download_url": artifact.get("download_url"),
    }


def _download_bytes(url: str) -> bytes:
    headers = {"Accept": "application/octet-stream"}
    with httpx.Client(timeout=120, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def verify_sha256(data: bytes, expected_hex: str) -> None:
    """Raise ValueError when the payload does not match the expected digest."""
    expected = (expected_hex or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("invalid expected SHA-256 digest")
    actual = sha256_hex(data)
    if not hmac.compare_digest(actual, expected):
        raise ValueError(
            f"SHA-256 mismatch (expected {expected[:12]}…, got {actual[:12]}…) — update aborted"
        )


def _is_protected(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return True
    if parts[0].lower() in PROTECTED_TOP_LEVEL:
        return True
    # Never clobber local env files anywhere in the tree.
    return relative.name.lower() == ".env"


def _root_matches_commit(source_root: Path, commit_sha: str | None) -> bool:
    """GitHub source archives unpack to `{owner}-{repo}-{fullsha}/`."""
    if not commit_sha:
        return True
    name = source_root.name.lower()
    sha = commit_sha.lower()
    return name.endswith(sha) or name.endswith(sha[:12]) or name.endswith(sha[:7])


def apply_tarball(
    tar_bytes: bytes,
    *,
    commit_sha: str | None = None,
    expect_commit_match: bool = False,
) -> dict[str, Any]:
    """
    Extract a GitHub source tarball and copy it over the install directory.

    User data (data/), credentials (.env), virtualenvs, and .git are untouched.
    Destinations that are symlinks (or would resolve outside PROJECT_ROOT) are
    skipped so a malicious archive cannot write through a symlink escape.
    """
    root = Path(config.PROJECT_ROOT).resolve()
    updated: list[str] = []
    with tempfile.TemporaryDirectory(prefix="paperless-update-") as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            tar.extractall(tmp_path, filter="data")

        # GitHub tarballs wrap everything in a single "{owner}-{repo}-{sha}/" dir.
        entries = [p for p in tmp_path.iterdir() if p.is_dir()]
        if len(entries) != 1:
            return {"status": "error", "error": "unexpected tarball layout"}
        source_root = entries[0]

        if expect_commit_match and not _root_matches_commit(source_root, commit_sha):
            return {
                "status": "error",
                "error": (
                    f"Archive root '{source_root.name}' does not match release "
                    f"commit {commit_sha} — update aborted"
                ),
            }

        for path in sorted(source_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source_root)
            if _is_protected(relative):
                continue
            dest = root / relative
            # Refuse to write through an existing symlink (could point outside).
            if dest.is_symlink() or any(parent.is_symlink() for parent in dest.parents):
                logger.warning("Skipping symlink destination during update: %s", relative)
                continue
            try:
                resolved = dest.resolve()
                if not resolved.is_relative_to(root):
                    logger.warning(
                        "Skipping path that escapes project root: %s", relative
                    )
                    continue
            except OSError:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            updated.append(str(relative))

    return {"status": "success", "updated_count": len(updated), "updated": updated}


def apply_update() -> dict[str, Any]:
    """Download the latest verified release and install it over the current version."""
    info = check_for_update()
    if info.get("status") != "success":
        return info
    if not info.get("update_available"):
        return {
            "status": "error",
            "error": f"Already up to date (v{info['current_version']}).",
        }
    if not info.get("verifiable") or not info.get("expected_sha256") or not info.get(
        "download_url"
    ):
        return {
            "status": "error",
            "error": info.get("verification_error")
            or "Refusing to install an unverified release (missing SHA-256).",
        }

    try:
        tar_bytes = _download_bytes(info["download_url"])
    except httpx.HTTPError as exc:
        return {"status": "error", "error": f"Download failed: {exc}"}

    try:
        verify_sha256(tar_bytes, info["expected_sha256"])
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}

    # Prefer commit-folder matching for GitHub-style archives; skip when the
    # publisher shipped a custom-prefixed root that still has a checksum.
    expect_commit = bool(info.get("commit_sha")) and "github.com" in (
        info.get("download_url") or ""
    )
    result = apply_tarball(
        tar_bytes,
        commit_sha=info.get("commit_sha"),
        expect_commit_match=expect_commit,
    )
    if result.get("status") != "success":
        return result

    return {
        **result,
        "installed_version": info.get("latest_version"),
        "previous_version": info["current_version"],
        "verified_sha256": info["expected_sha256"],
        "artifact_name": info.get("artifact_name"),
        "restart_required": True,
    }


def schedule_restart(delay_seconds: float = 0.75) -> dict[str, Any]:
    """
    Restart the server process in-place after a short delay.

    Re-executes the original command line (works for `uvicorn …` console
    scripts and `python -m uvicorn …` alike), so the response below can still
    be delivered before the process is replaced.
    """
    argv = [sys.executable, *sys.argv]

    def _restart() -> None:
        logger.info("Restarting: %s", " ".join(argv))
        os.execv(sys.executable, argv)  # noqa: S606

    timer = threading.Timer(delay_seconds, _restart)
    timer.daemon = True
    timer.start()
    return {"status": "success", "message": "Restarting…", "command": argv}
