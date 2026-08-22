"""Tests for systemd autostart helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from deepcatalog import system_service


def test_render_unit_file_includes_paths(tmp_path, monkeypatch):
    project = tmp_path / "app"
    project.mkdir()
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv_bin / "uvicorn").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("DEEPCATALOG_HOST", "127.0.0.1")
    monkeypatch.setenv("DEEPCATALOG_PORT", "8080")
    monkeypatch.setattr(system_service.config, "PROJECT_ROOT", project)
    monkeypatch.setattr(system_service.config, "DATA_DIR", project / "data")

    text = system_service.render_unit_file()
    assert f"WorkingDirectory={project}" in text
    assert (
        f"ExecStart={venv_bin / 'python'} -m deepcatalog.serve --host 127.0.0.1 --port 8080" in text
    )
    assert "Environment=DEEPCATALOG_SYSTEMD=1" in text
    assert "WantedBy=default.target" in text


def test_render_unit_file_appimage(tmp_path, monkeypatch):
    image = tmp_path / "DeepCatalog-x86_64.AppImage"
    image.write_bytes(b"fake")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setenv("DEEPCATALOG_APPIMAGE", "1")
    monkeypatch.setenv("DEEPCATALOG_HOST", "127.0.0.1")
    monkeypatch.setenv("DEEPCATALOG_PORT", "8080")
    monkeypatch.setattr(system_service.config, "PROJECT_ROOT", tmp_path / "opt")
    monkeypatch.setattr(system_service.config, "DATA_DIR", data)

    text = system_service.render_unit_file()
    assert f"ExecStart={image} --headless --host 127.0.0.1 --port 8080" in text
    assert f"WorkingDirectory={data}" in text
    assert "Environment=DEEPCATALOG_APPIMAGE=1" in text
    assert "uvicorn" not in text


def test_autostart_status_unsupported_on_non_linux(monkeypatch):
    monkeypatch.setattr(system_service, "_is_linux", lambda: False)
    status = system_service.autostart_status()
    assert status["supported"] is False
    assert "Linux" in (status.get("install_hint") or "")


def test_set_autostart_enable_writes_unit_and_enables(tmp_path, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv_bin / "uvicorn").write_text("#!/bin/sh\n", encoding="utf-8")
    unit_file = tmp_path / "systemd-user" / system_service.UNIT_NAME

    monkeypatch.setenv("DEEPCATALOG_HOST", "127.0.0.1")
    monkeypatch.setenv("DEEPCATALOG_PORT", "8080")
    monkeypatch.setattr(system_service.config, "PROJECT_ROOT", project)
    monkeypatch.setattr(system_service.config, "DATA_DIR", project / "data")
    monkeypatch.setattr(system_service, "_systemd_available", lambda: True)
    monkeypatch.setattr(system_service, "_unit_enabled", lambda: False)
    monkeypatch.setattr(system_service, "_unit_active", lambda: False)
    monkeypatch.setattr(system_service, "_linger_enabled", lambda: False)
    monkeypatch.setattr(system_service, "_port_is_free", lambda *_a, **_k: True)
    monkeypatch.setattr(system_service, "unit_path", lambda: unit_file)

    calls: list[list[str]] = []

    def fake_systemctl(*args: str, timeout: float = 30.0):
        calls.append(list(args))
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""
        return completed

    monkeypatch.setattr(system_service, "_systemctl", fake_systemctl)
    monkeypatch.setattr(system_service, "_loginctl", lambda *_a, **_k: MagicMock(returncode=0))

    result = system_service.set_autostart(True)
    assert result["status"] == "success"
    assert unit_file.is_file()
    assert "DeepCatalog — deep document intelligence, local-first." in unit_file.read_text(
        encoding="utf-8"
    )
    assert ["daemon-reload"] in calls
    assert ["enable", system_service.UNIT_NAME] in calls
    assert ["start", system_service.UNIT_NAME] in calls


def test_autostart_status_appimage_skips_venv(tmp_path, monkeypatch):
    image = tmp_path / "DeepCatalog.AppImage"
    image.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setenv("DEEPCATALOG_APPIMAGE", "1")
    monkeypatch.setattr(system_service, "_systemd_available", lambda: True)
    monkeypatch.setattr(system_service, "_unit_enabled", lambda: False)
    monkeypatch.setattr(system_service, "_unit_active", lambda: False)
    monkeypatch.setattr(system_service, "_linger_enabled", lambda: False)
    monkeypatch.setattr(system_service.config, "PROJECT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(system_service.config, "DATA_DIR", tmp_path / "data")

    status = system_service.autostart_status()
    assert status["supported"] is True
    assert status["error"] is None


def test_autostart_status_appimage_missing_path(tmp_path, monkeypatch):
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "missing.AppImage"))
    monkeypatch.setenv("DEEPCATALOG_APPIMAGE", "1")
    monkeypatch.setattr(system_service, "_systemd_available", lambda: True)
    monkeypatch.setattr(system_service, "_linger_enabled", lambda: False)
    monkeypatch.setattr(system_service.config, "PROJECT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(system_service.config, "DATA_DIR", tmp_path / "data")

    status = system_service.autostart_status()
    assert status["supported"] is True
    assert status["error"]
    assert "APPIMAGE" in status["error"]


def test_set_autostart_disable_calls_systemctl(tmp_path, monkeypatch):
    project = tmp_path / "repo"
    project.mkdir()
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv_bin / "uvicorn").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(system_service.config, "PROJECT_ROOT", project)
    monkeypatch.setattr(system_service, "_systemd_available", lambda: True)

    calls: list[list[str]] = []

    def fake_systemctl(*args: str, timeout: float = 30.0):
        calls.append(list(args))
        completed = MagicMock()
        completed.returncode = 0
        completed.stdout = ""
        completed.stderr = ""
        return completed

    monkeypatch.setattr(system_service, "_systemctl", fake_systemctl)

    result = system_service.set_autostart(False)
    assert result["status"] == "success"
    assert ["disable", system_service.UNIT_NAME] in calls


def test_autostart_api(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.settings.autostart_status",
        lambda: {
            "supported": True,
            "enabled": True,
            "active": True,
            "url": "http://127.0.0.1:8080",
            "error": None,
        },
    )
    resp = client.get("/api/autostart/status")
    assert resp.status_code == 200
    assert resp.json()["autostart"]["enabled"] is True


def test_autostart_toggle_api(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.settings.set_autostart",
        lambda enabled: {
            "status": "success",
            "autostart": {"supported": True, "enabled": enabled, "active": enabled},
        },
    )
    resp = client.post("/api/autostart", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["autostart"]["enabled"] is True


def test_port_probe_host_never_uses_wildcard():
    from deepcatalog.local_security import port_probe_host

    assert port_probe_host("") == "127.0.0.1"
    assert port_probe_host("0.0.0.0") == "127.0.0.1"
    assert port_probe_host("*") == "127.0.0.1"
    assert port_probe_host("::") == "127.0.0.1"
    assert port_probe_host("127.0.0.1") == "127.0.0.1"
    assert port_probe_host("192.168.0.5") == "192.168.0.5"
