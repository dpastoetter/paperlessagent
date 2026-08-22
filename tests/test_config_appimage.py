"""Tests for AppImage-related config helpers."""

from __future__ import annotations

from paperless_agent.config import resolve_project_root, running_as_appimage


def test_resolve_project_root_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERLESS_PROJECT_ROOT", str(tmp_path))
    assert resolve_project_root() == tmp_path.resolve()


def test_running_as_appimage_flag(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("PAPERLESS_APPIMAGE", raising=False)
    assert running_as_appimage() is False
    monkeypatch.setenv("PAPERLESS_APPIMAGE", "1")
    assert running_as_appimage() is True


def test_running_as_appimage_runtime_path(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPERLESS_APPIMAGE", raising=False)
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "PaperlessAgent.AppImage"))
    assert running_as_appimage() is True
