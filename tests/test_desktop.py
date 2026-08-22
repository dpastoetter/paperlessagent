"""Tests for the desktop shell helpers (no GUI)."""

from __future__ import annotations

import logging
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pytest

from deepcatalog.desktop import (
    WM_CLASS,
    _launch_ui_window,
    _pick_port,
    _project_root,
    _try_native_window,
    chromium_app_argv,
    chromium_profile_dir,
    desktop_exec_command,
    desktop_ui_url,
    find_chromium_app_browser,
    health_url,
    install_linux_desktop_entry,
    is_server_healthy,
    main,
    open_chromium_app_window,
    prefer_chromium_app_window,
    render_desktop_entry,
    wait_for_health,
    window_icon_path,
)


def test_pick_port_returns_preferred():
    assert _pick_port("127.0.0.1", 9090) == 9090


def test_pick_port_returns_positive():
    port = _pick_port("127.0.0.1", None)
    assert isinstance(port, int)
    assert port > 0


def test_wait_for_health_succeeds():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_for_health("127.0.0.1", port, timeout=5.0)
        assert is_server_healthy("127.0.0.1", port)
        assert health_url("127.0.0.1", port).endswith(f":{port}/api/health")
    finally:
        server.shutdown()


def test_wait_for_health_times_out():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    # Port is not listening after the socket closes — health must time out.
    with pytest.raises(TimeoutError, match="did not become ready"):
        wait_for_health("127.0.0.1", port, timeout=0.4)


def test_project_root_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPCATALOG_PROJECT_ROOT", str(tmp_path))
    assert _project_root() == tmp_path.resolve()


def test_main_passes_headless(monkeypatch):
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("deepcatalog.desktop.run_desktop", fake_run)
    assert main(["--headless", "--port", "9999"]) == 0
    assert captured["headless"] is True
    assert captured["port"] == 9999


def test_render_desktop_entry_sets_wm_class():
    text = render_desktop_entry(
        exec_line="/opt/DeepCatalog.AppImage",
        icon="/tmp/deepcatalog.png",
    )
    assert f"StartupWMClass={WM_CLASS}" in text
    assert "Exec=/opt/DeepCatalog.AppImage" in text
    assert "Icon=/tmp/deepcatalog.png" in text
    assert "Terminal=false" in text


def test_desktop_exec_command_uses_appimage(tmp_path, monkeypatch):
    image = tmp_path / "DeepCatalog-x86_64.AppImage"
    image.write_bytes(b"fake")
    monkeypatch.setenv("APPIMAGE", str(image))
    assert desktop_exec_command() == str(image.resolve())


def test_desktop_exec_command_quotes_spaces(tmp_path, monkeypatch):
    image = tmp_path / "DeepCatalog App.AppImage"
    image.write_bytes(b"fake")
    monkeypatch.setenv("APPIMAGE", str(image))
    quoted = desktop_exec_command()
    assert quoted.startswith('"')
    assert "DeepCatalog App.AppImage" in quoted


def test_chromium_app_argv_uses_isolated_profile_and_class(tmp_path):
    profile = chromium_profile_dir(tmp_path)
    argv = chromium_app_argv(
        "/usr/bin/brave-browser",
        "http://127.0.0.1:8080/",
        profile,
        width=1280,
        height=840,
    )
    assert argv[0] == "/usr/bin/brave-browser"
    assert "--app=http://127.0.0.1:8080/" in argv
    assert f"--user-data-dir={profile}" in argv
    assert "--profile-directory=Default" in argv
    assert f"--class={WM_CLASS}" in argv
    assert "--ozone-platform-hint=x11" in argv
    assert profile.is_dir()


def test_find_chromium_app_browser_prefers_brave(monkeypatch):
    def fake_which(name: str) -> str | None:
        mapping = {"brave-browser": "/usr/bin/brave-browser", "chromium": "/usr/bin/chromium"}
        return mapping.get(name)

    monkeypatch.setattr("deepcatalog.desktop.shutil.which", fake_which)
    assert find_chromium_app_browser() == "/usr/bin/brave-browser"


def test_open_chromium_app_window_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deepcatalog.desktop.find_chromium_app_browser",
        lambda: "/usr/bin/brave-browser",
    )

    class Proc:
        returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="brave", timeout=timeout)
            return 0

    monkeypatch.setattr("deepcatalog.desktop.subprocess.Popen", lambda *a, **k: Proc())
    result = open_chromium_app_window(
        "http://127.0.0.1:8080/",
        data_dir=tmp_path,
        width=800,
        height=600,
    )
    assert result == "closed"


def test_open_chromium_app_window_detached(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deepcatalog.desktop.find_chromium_app_browser",
        lambda: "/usr/bin/brave-browser",
    )

    class Proc:
        returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr("deepcatalog.desktop.subprocess.Popen", lambda *a, **k: Proc())
    result = open_chromium_app_window(
        "http://127.0.0.1:8080/",
        data_dir=tmp_path,
        width=800,
        height=600,
    )
    assert result == "detached"


def test_open_chromium_app_window_missing_browser(tmp_path, monkeypatch):
    monkeypatch.setattr("deepcatalog.desktop.find_chromium_app_browser", lambda: None)
    assert (
        open_chromium_app_window(
            "http://127.0.0.1:8080/",
            data_dir=tmp_path,
            width=800,
            height=600,
        )
        is None
    )


def test_install_linux_desktop_entry(tmp_path, monkeypatch):
    image = tmp_path / "DeepCatalog.AppImage"
    image.write_bytes(b"fake")
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setattr("deepcatalog.desktop.sys.platform", "linux")
    monkeypatch.setattr("deepcatalog.desktop.shutil.which", lambda _name: None)

    dest = install_linux_desktop_entry()
    assert dest == xdg / "applications" / "deepcatalog.desktop"
    text = dest.read_text(encoding="utf-8")
    assert f"StartupWMClass={WM_CLASS}" in text
    assert str(image.resolve()) in text
    icons = list((xdg / "icons").rglob("deepcatalog.*"))
    assert icons, "expected themed icon files"


def test_window_icon_path_prefers_appdir_png(tmp_path, monkeypatch):
    png = tmp_path / "deepcatalog.png"
    png.write_bytes(b"png")
    monkeypatch.setenv("APPDIR", str(tmp_path))
    assert window_icon_path() == png


def test_try_native_window_logs_import_error(monkeypatch, caplog):
    monkeypatch.delitem(sys.modules, "webview", raising=False)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview":
            raise ImportError("no webview")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with caplog.at_level(logging.WARNING, logger="deepcatalog.desktop"):
        assert _try_native_window("http://127.0.0.1:8080/", 800, 600) is False
    assert "pywebview is not available" in caplog.text


def test_try_native_window_logs_webkit_failure(monkeypatch, caplog):
    webview = MagicMock()
    webview.create_window.side_effect = RuntimeError("WebKit2 typelib missing")
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr("deepcatalog.desktop._apply_gtk_wm_class", lambda: None)
    with caplog.at_level(logging.ERROR, logger="deepcatalog.desktop"):
        assert _try_native_window("http://127.0.0.1:8080/", 800, 600) is False
    assert "Native WebKitGTK window failed" in caplog.text


def test_desktop_ui_url_marks_app_shell():
    assert desktop_ui_url("127.0.0.1", 8080) == "http://127.0.0.1:8080/?desktop=1"


def test_prefer_chromium_app_window_for_appimage(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("DEEPCATALOG_APPIMAGE", raising=False)
    assert prefer_chromium_app_window() is False
    monkeypatch.setenv("DEEPCATALOG_APPIMAGE", "1")
    assert prefer_chromium_app_window() is True


def test_appimage_launch_uses_chromium_before_webview(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPCATALOG_APPIMAGE", "1")
    monkeypatch.setattr(
        "deepcatalog.desktop.open_chromium_app_window",
        lambda *_a, **_k: "detached",
    )

    def fail_webview(*_a, **_k):
        raise AssertionError("AppImage must not wait on pywebview")

    monkeypatch.setattr("deepcatalog.desktop._try_native_window", fail_webview)
    assert (
        _launch_ui_window(
            "http://127.0.0.1:8080/?desktop=1",
            data_dir=tmp_path,
            width=800,
            height=600,
        )
        is False
    )


def test_appimage_launch_falls_back_to_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPCATALOG_APPIMAGE", "1")
    opened: list[str] = []
    monkeypatch.setattr("deepcatalog.desktop.open_chromium_app_window", lambda *_a, **_k: None)
    monkeypatch.setattr("deepcatalog.desktop._try_native_window", lambda *_a, **_k: False)
    monkeypatch.setattr(
        "deepcatalog.desktop._open_in_browser",
        lambda url: opened.append(url),
    )
    assert (
        _launch_ui_window(
            "http://127.0.0.1:8080/?desktop=1",
            data_dir=tmp_path,
            width=800,
            height=600,
        )
        is False
    )
    assert opened == ["http://127.0.0.1:8080/?desktop=1"]
