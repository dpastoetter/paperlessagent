"""Install and manage a systemd user service for boot-time autostart."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from paperless_agent import config
from paperless_agent.config import running_as_appimage
from paperless_agent.local_security import assert_bind_allowed, ssl_cert_paths

UNIT_NAME = "paperlessagent.service"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _run(
    args: list[str],
    *,
    timeout: float = 30.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def _systemctl(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return _run(["systemctl", "--user", *args], timeout=timeout)


def _loginctl(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return _run(["loginctl", *args], timeout=timeout)


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def uvicorn_binary() -> Path:
    return config.PROJECT_ROOT / ".venv" / "bin" / "uvicorn"


def appimage_exec_path() -> str | None:
    """Absolute path of the running AppImage, if this process was launched from one."""
    path = os.getenv("APPIMAGE", "").strip()
    if path and Path(path).is_file():
        return path
    return None


def service_host() -> str:
    return (os.getenv("PAPERLESS_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST


def service_port() -> int:
    raw = (os.getenv("PAPERLESS_PORT") or str(DEFAULT_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if port > 0 else DEFAULT_PORT


def service_url() -> str:
    return f"http://{service_host()}:{service_port()}"


def _systemd_available() -> bool:
    if not _is_linux():
        return False
    if not shutil.which("systemctl"):
        return False
    try:
        completed = _systemctl("status", timeout=5.0)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    # status exits non-zero when the queried unit is missing; that still means systemd works.
    return completed.returncode in {0, 3, 4}


def _unit_enabled() -> bool:
    try:
        completed = _systemctl("is-enabled", UNIT_NAME, timeout=5.0)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "enabled"


def _unit_active() -> bool:
    try:
        completed = _systemctl("is-active", UNIT_NAME, timeout=5.0)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "active"


def _linger_enabled() -> bool:
    if not shutil.which("loginctl"):
        return False
    try:
        completed = _loginctl(
            "show-user", os.getenv("USER") or "", "--property=Linger", timeout=5.0
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if completed.returncode != 0:
        return False
    for line in completed.stdout.splitlines():
        if line.startswith("Linger="):
            return line.split("=", 1)[1].strip().lower() == "yes"
    return False


def _managed_by_systemd() -> bool:
    return os.getenv("PAPERLESS_SYSTEMD", "").strip() == "1"


def _extra_environment_lines() -> list[str]:
    lines = [
        f"Environment=DATA_DIR={config.DATA_DIR}",
        "Environment=PAPERLESS_SYSTEMD=1",
    ]
    if running_as_appimage():
        lines.append("Environment=PAPERLESS_APPIMAGE=1")
        env_file = Path(config.DATA_DIR) / ".env"
    else:
        env_file = config.PROJECT_ROOT / ".env"
    if env_file.is_file():
        lines.append(f"EnvironmentFile=-{env_file}")
    ollama_lib = Path.home() / ".local" / "lib" / "ollama"
    if ollama_lib.is_dir():
        lines.append(f"Environment=LD_LIBRARY_PATH={ollama_lib}")
    return lines


def render_unit_file() -> str:
    """Render the systemd user unit for the current install."""
    appimage = appimage_exec_path()
    host = service_host()
    port = service_port()
    env_lines = "\n".join(_extra_environment_lines())
    if appimage:
        workdir = config.DATA_DIR
        exec_start = f"{appimage} --headless --host {host} --port {port}"
        return f"""[Unit]
Description=PaperlessAgent local document assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={workdir}
{env_lines}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""

    uvicorn = uvicorn_binary()
    workdir = config.PROJECT_ROOT
    ssl_paths = ssl_cert_paths()
    ssl_args = ""
    if ssl_paths:
        cert, key = ssl_paths
        ssl_args = f" --ssl-certfile {cert} --ssl-keyfile {key}"
    return f"""[Unit]
Description=PaperlessAgent local document assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={workdir}
{env_lines}
ExecStart={uvicorn} app.main:app --host {host} --port {port}{ssl_args}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _install_unit() -> None:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_unit_file(), encoding="utf-8")


def _daemon_reload() -> None:
    completed = _systemctl("daemon-reload")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or "systemctl --user daemon-reload failed")


def _enable_linger() -> None:
    if not shutil.which("loginctl"):
        return
    user = os.getenv("USER") or ""
    if not user:
        return
    completed = _loginctl("enable-linger", user)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or "loginctl enable-linger failed")


def _port_is_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def autostart_status() -> dict[str, Any]:
    """Return autostart / systemd status for the Settings UI."""
    supported = _systemd_available()
    uvicorn = uvicorn_binary()
    status: dict[str, Any] = {
        "supported": supported,
        "platform": sys.platform,
        "unit_name": UNIT_NAME,
        "unit_path": str(unit_path()),
        "enabled": False,
        "active": False,
        "installed": unit_path().is_file(),
        "linger": _linger_enabled() if supported else False,
        "managed_by_service": _managed_by_systemd(),
        "host": service_host(),
        "port": service_port(),
        "url": service_url(),
        "project_root": str(config.PROJECT_ROOT),
        "uvicorn_path": str(uvicorn),
        "error": None,
        "install_hint": None,
    }
    if not supported:
        status["install_hint"] = "Autostart requires Linux with systemd user services."
        return status
    if running_as_appimage():
        if not appimage_exec_path():
            status["error"] = (
                "APPIMAGE path is missing; cannot install autostart from an extracted image."
            )
            status["install_hint"] = (
                "Launch PaperlessAgent from the .AppImage file, then enable autostart."
            )
            return status
        status["enabled"] = _unit_enabled()
        status["active"] = _unit_active()
        return status
    if not uvicorn.is_file():
        status["error"] = (
            f"Virtualenv not found at {uvicorn}. "
            "Run the installer or create .venv before enabling autostart."
        )
        status["install_hint"] = (
            f"From {config.PROJECT_ROOT}: python3 -m venv .venv && "
            ".venv/bin/pip install -e . -c constraints.txt"
        )
        return status
    status["enabled"] = _unit_enabled()
    status["active"] = _unit_active()
    return status


def set_autostart(enabled: bool) -> dict[str, Any]:
    """Enable or disable the systemd user service for PaperlessAgent."""
    status = autostart_status()
    if not status["supported"]:
        raise RuntimeError(
            status.get("install_hint") or "Autostart is not supported on this system."
        )
    if status.get("error"):
        raise RuntimeError(str(status["error"]))

    if enabled:
        assert_bind_allowed(service_host())
        _install_unit()
        _daemon_reload()
        completed = _systemctl("enable", UNIT_NAME)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or f"systemctl --user enable {UNIT_NAME} failed")
        _enable_linger()
        host = service_host()
        port = service_port()
        if not _unit_active():
            if _port_is_free(host, port):
                start_completed = _systemctl("start", UNIT_NAME)
                if start_completed.returncode != 0:
                    detail = (start_completed.stderr or start_completed.stdout or "").strip()
                    raise RuntimeError(detail or f"systemctl --user start {UNIT_NAME} failed")
            # If the port is already in use, leave the current instance running.
    else:
        completed = _systemctl("disable", UNIT_NAME)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or f"systemctl --user disable {UNIT_NAME} failed")

    return {"status": "success", "autostart": autostart_status()}
