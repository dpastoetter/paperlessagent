"""Tests for the GitHub self-update mechanism (no network)."""

from __future__ import annotations

import hashlib
import io
import tarfile

import pytest

from paperless_agent.updater import (
    apply_tarball,
    apply_update,
    get_current_version,
    is_newer,
    parse_sha256sums,
    parse_version,
    sha256_hex,
    verify_sha256,
)


def test_parse_and_compare_versions():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("0.1.0") == (0, 1, 0)
    assert parse_version("garbage") == (0,)

    assert is_newer("v0.2.0", "0.1.0")
    assert is_newer("1.0.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("v0.0.9", "0.1.0")


def test_get_current_version_reads_pyproject():
    version = get_current_version()
    assert parse_version(version) > (0,) or version == "0.1.0"


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
    monkeypatch.setattr(
        "paperless_agent.updater._download_bytes", lambda _url: tarball
    )
    result = apply_update()
    assert result["status"] == "error"
    assert "mismatch" in result["error"].lower()
    assert not (isolated_root / "pyproject.toml").exists()


def test_apply_update_installs_verified_release(isolated_root, monkeypatch):
    tarball = _make_tarball({"pyproject.toml": b'[project]\nversion = "9.9.9"\n'})
    digest = hashlib.sha256(tarball).hexdigest()
    monkeypatch.setattr(
        "paperless_agent.updater.check_for_update",
        lambda: {
            "status": "success",
            "current_version": "0.1.0",
            "latest_version": "9.9.9",
            "update_available": True,
            "verifiable": True,
            "download_url": "https://example.invalid/paperlessagent-9.9.9.tar.gz",
            "expected_sha256": digest,
            "artifact_name": "paperlessagent-9.9.9.tar.gz",
            "commit_sha": None,
        },
    )
    monkeypatch.setattr(
        "paperless_agent.updater._download_bytes", lambda _url: tarball
    )
    result = apply_update()
    assert result["status"] == "success"
    assert result["restart_required"] is True
    assert result["installed_version"] == "9.9.9"
    assert result["verified_sha256"] == digest
    assert (isolated_root / "pyproject.toml").exists()
