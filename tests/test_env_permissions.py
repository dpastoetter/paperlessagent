"""Tests for restrictive .env / secret-file permissions."""

from __future__ import annotations

import stat

from paperless_agent.env_permissions import (
    SECRET_FILE_MODE,
    ensure_dotenv_permissions,
    harden_secret_file,
    is_group_or_world_accessible,
    write_secret_text,
)


def test_write_secret_text_sets_0600(tmp_path):
    path = tmp_path / ".env"
    write_secret_text(path, "PAPERLESS_API_TOKEN=secret\n")
    assert path.read_text(encoding="utf-8") == "PAPERLESS_API_TOKEN=secret\n"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == SECRET_FILE_MODE
    assert not is_group_or_world_accessible(path)


def test_harden_secret_file_fixes_world_readable(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
    path.chmod(0o644)
    assert is_group_or_world_accessible(path)
    report = harden_secret_file(path, fix=True)
    assert report["was_insecure"] is True
    assert report["fixed"] is True
    assert not is_group_or_world_accessible(path)
    assert stat.S_IMODE(path.stat().st_mode) == SECRET_FILE_MODE


def test_harden_secret_file_reports_without_fix(tmp_path):
    path = tmp_path / ".env"
    path.write_text("x=1\n", encoding="utf-8")
    path.chmod(0o666)
    report = harden_secret_file(path, fix=False)
    assert report["was_insecure"] is True
    assert report["fixed"] is False
    assert is_group_or_world_accessible(path)


def test_ensure_dotenv_permissions_fixes_project_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("PAPERLESS_API_TOKEN=tok\n", encoding="utf-8")
    env_path.chmod(0o664)
    monkeypatch.setattr(
        "paperless_agent.env_permissions.candidate_env_paths",
        lambda: [env_path],
    )
    reports = ensure_dotenv_permissions(fix=True)
    assert len(reports) == 1
    assert reports[0]["was_insecure"] is True
    assert reports[0]["fixed"] is True
    assert stat.S_IMODE(env_path.stat().st_mode) == SECRET_FILE_MODE
