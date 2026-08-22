"""Tests for the GitHub self-update mechanism (no network)."""

from __future__ import annotations

import hashlib
import io
import tarfile

import httpx
import pytest

import paperless_agent
from app.main import app
from paperless_agent.updater import (
    _github_get,
    _pick_appimage_asset,
    apply_tarball,
    apply_update,
    is_newer,
    parse_sha256sums,
    parse_version,
    sha256_hex,
    verify_sha256,
)
from paperless_agent.version import clear_version_cache
from paperless_agent.version import get_current_version as read_version


def test_parse_and_compare_versions():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("0.1.0") == (0, 1, 0)
    assert parse_version("garbage") == (0,)

    assert is_newer("v0.2.0", "0.1.0")
    assert is_newer("1.0.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("v0.0.9", "0.1.0")


def test_get_current_version_reads_pyproject():
    clear_version_cache()
    version = read_version()
    assert parse_version(version) > (0,) or version == "0.1.0"
    # FastAPI OpenAPI metadata must track the same version resolution path.
    assert app.version == version
    assert paperless_agent.__version__ == version


def test_parse_sha256sums_indexes_basename():
    text = (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  "
        "dist/paperlessagent-1.0.0.tar.gz\n"
        "# comment\n"
    )
    mapping = parse_sha256sums(text)
    assert (
        mapping["paperlessagent-1.0.0.tar.gz"]
        == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )


def test_verify_sha256_accepts_match_and_rejects_mismatch():
    payload = b"hello-update"
    digest = sha256_hex(payload)
    verify_sha256(payload, digest)
    with pytest.raises(ValueError, match="mismatch"):
        verify_sha256(payload, "0" * 64)


def test_github_get_retries_remote_protocol_error(monkeypatch):
    calls = {"n": 0}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}

    def fake_get(_self, _url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return FakeResponse()

    monkeypatch.setattr("time.sleep", lambda _s: None)
    with httpx.Client() as client:
        monkeypatch.setattr(client, "get", fake_get.__get__(client, httpx.Client))
        resp = _github_get(client, "https://api.github.com/example")
    assert resp.status_code == 200
    assert calls["n"] == 2


def test_github_get_retries_http_503(monkeypatch):
    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.headers = {}

        @staticmethod
        def json():
            return {}

    def fake_get(_self, _url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(503)
        return FakeResponse(200)

    monkeypatch.setattr(__import__("time"), "sleep", lambda _s: None)
    with httpx.Client() as client:
        monkeypatch.setattr(client, "get", fake_get.__get__(client, httpx.Client))
        resp = _github_get(client, "https://api.github.com/example")
    assert resp.status_code == 200
    assert calls["n"] == 2


def _make_tarball(files: dict[str, bytes], root: str = "owner-repo-abc123") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=f"{root}/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


@pytest.fixture()
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr("paperless_agent.config.PROJECT_ROOT", tmp_path)
    return tmp_path


def test_apply_tarball_updates_code_but_protects_user_data(isolated_root):
    # Existing local state that must survive an update.
    (isolated_root / "data").mkdir()
    (isolated_root / "data" / "paperless.db").write_bytes(b"precious")
    (isolated_root / ".env").write_text("OPENAI_API_KEY=secret\n")
    (isolated_root / "app").mkdir()
    (isolated_root / "app" / "main.py").write_text("old code\n")

    tarball = _make_tarball(
        {
            "app/main.py": b"new code\n",
            "paperless_agent/new_module.py": b"print('hi')\n",
            "pyproject.toml": b'[project]\nversion = "0.2.0"\n',
            "data/paperless.db": b"attacker data",
            ".env": b"OPENAI_API_KEY=evil",
        }
    )

    result = apply_tarball(tarball)
    assert result["status"] == "success"
    assert result["updated_count"] == 3

    assert (isolated_root / "app" / "main.py").read_text() == "new code\n"
    assert (isolated_root / "paperless_agent" / "new_module.py").exists()
    # Protected paths untouched.
    assert (isolated_root / "data" / "paperless.db").read_bytes() == b"precious"
    assert "secret" in (isolated_root / ".env").read_text()


def test_apply_tarball_prunes_obsolete_release_files(isolated_root):
    (isolated_root / "app").mkdir()
    (isolated_root / "app" / "main.py").write_text("old\n")
    (isolated_root / "legacy.py").write_text("stale\n")
    (isolated_root / ".release-files").write_text("app/main.py\nlegacy.py\n")
    (isolated_root / "data").mkdir()
    (isolated_root / "data" / "keep.db").write_bytes(b"db")

    tarball = _make_tarball(
        {
            "app/main.py": b"new\n",
            ".release-files": b"app/main.py\n",
            ".release-commit": b"tag=v9.9.9\n",
        }
    )
    result = apply_tarball(tarball)
    assert result["status"] == "success"
    assert result["removed_count"] == 1
    assert "legacy.py" in result["removed"]
    assert not (isolated_root / "legacy.py").exists()
    assert (isolated_root / "app" / "main.py").read_text() == "new\n"
    assert (isolated_root / "data" / "keep.db").read_bytes() == b"db"
    assert (isolated_root / ".release-files").read_text() == "app/main.py\n"


def test_apply_tarball_rejects_unexpected_layout(isolated_root):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="loose-file.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"xx"))
    result = apply_tarball(buffer.getvalue())
    assert result["status"] == "error"


def test_apply_tarball_rejects_commit_mismatch(isolated_root):
    tarball = _make_tarball({"pyproject.toml": b"version=1\n"}, root="owner-repo-deadbeef")
    result = apply_tarball(
        tarball,
        commit_sha="c" * 40,
        expect_commit_match=True,
    )
    assert result["status"] == "error"
    assert "does not match release commit" in result["error"]


def test_apply_update_refuses_when_up_to_date(monkeypatch):
    monkeypatch.setattr(
        "paperless_agent.updater.check_for_update",
        lambda: {
            "status": "success",
            "current_version": "0.1.0",
            "latest_version": "0.1.0",
            "download_url": "https://example.invalid/tarball",
            "expected_sha256": "a" * 64,
            "verifiable": True,
            "update_available": False,
        },
    )
    result = apply_update()
    assert result["status"] == "error"
    assert "up to date" in result["error"]


def test_apply_update_refuses_unverified_release(monkeypatch):
    monkeypatch.setattr(
        "paperless_agent.updater.check_for_update",
        lambda: {
            "status": "success",
            "current_version": "0.1.0",
            "latest_version": "9.9.9",
            "update_available": True,
            "verifiable": False,
            "verification_error": "missing SHA-256",
        },
    )
    result = apply_update()
    assert result["status"] == "error"
    assert "missing SHA-256" in result["error"]


def test_apply_update_refuses_checksum_mismatch(isolated_root, monkeypatch):
    tarball = _make_tarball({"pyproject.toml": b'[project]\nversion = "9.9.9"\n'})
    monkeypatch.setattr(
        "paperless_agent.updater.check_for_update",
        lambda: {
            "status": "success",
            "current_version": "0.1.0",
            "latest_version": "9.9.9",
            "update_available": True,
            "verifiable": True,
            "download_url": "https://example.invalid/paperlessagent-9.9.9.tar.gz",
            "expected_sha256": "0" * 64,
            "artifact_name": "paperlessagent-9.9.9.tar.gz",
        },
    )
    monkeypatch.setattr("paperless_agent.updater._download_bytes", lambda _url: tarball)
    result = apply_update()
    assert result["status"] == "error"
    assert "mismatch" in result["error"].lower()
    assert not (isolated_root / "pyproject.toml").exists()


def test_apply_update_installs_verified_release(isolated_root, monkeypatch):
    tarball = _make_tarball(
        {"pyproject.toml": b'[project]\nversion = "9.9.9"\n'},
        root="paperlessagent-9.9.9",
    )
    digest = hashlib.sha256(tarball).hexdigest()
    monkeypatch.setattr(
        "paperless_agent.updater.check_for_update",
        lambda: {
            "status": "success",
            "current_version": "0.1.0",
            "latest_version": "9.9.9",
            "update_available": True,
            "verifiable": True,
            "download_url": (
                "https://github.com/dpastoetter/paperlessagent/releases/download/"
                "v9.9.9/paperlessagent-9.9.9.tar.gz"
            ),
            "expected_sha256": digest,
            "artifact_name": "paperlessagent-9.9.9.tar.gz",
            # Commit SHA is present for the release tag, but versioned archive
            # roots must still install after checksum verification.
            "commit_sha": "a" * 40,
        },
    )
    monkeypatch.setattr("paperless_agent.updater._download_bytes", lambda _url: tarball)
    result = apply_update()
    assert result["status"] == "success"
    assert result["restart_required"] is True
    assert result["installed_version"] == "9.9.9"
    assert result["verified_sha256"] == digest
    assert (isolated_root / "pyproject.toml").exists()


def test_pick_appimage_prefers_x86_64():
    chosen = _pick_appimage_asset(
        [
            {"name": "PaperlessAgent-1.0.0-aarch64.AppImage"},
            {"name": "PaperlessAgent-1.0.0-x86_64.AppImage"},
        ]
    )
    assert chosen is not None
    assert chosen["name"].endswith("x86_64.AppImage")


def test_apply_update_refuses_appimage(monkeypatch):
    monkeypatch.setattr("paperless_agent.updater.running_as_appimage", lambda: True)
    result = apply_update()
    assert result["status"] == "error"
    assert result["installable"] is False
    assert "AppImage" in result["error"]
