"""Native desktop window shell around the local FastAPI UI (pywebview)."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from paperless_agent.local_security import assert_bind_allowed

DEFAULT_HOST = "127.0.0.1"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 840
HEALTH_TIMEOUT_S = 30.0
HEALTH_POLL_S = 0.15


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "paperlessagent"


def _prepare_environment() -> Path:
    """Ensure DATA_DIR / optional per-user .env are ready before importing the app."""
    data_dir = Path(os.environ.get("DATA_DIR", _default_data_dir())).expanduser()
    os.environ["DATA_DIR"] = str(data_dir.resolve())
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (data_dir / "archive").mkdir(parents=True, exist_ok=True)
    (data_dir / "chroma").mkdir(parents=True, exist_ok=True)

    env_file = data_dir / ".env"
    example = _project_root() / ".env.example"
    if not env_file.exists() and example.exists():
        env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    if env_file.exists():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    return data_dir


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, preferred: int | None) -> int:
    if preferred and preferred > 0 and _port_is_free(host, preferred):
        return preferred
    if _port_is_free(host, 8080):
        return 8080
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def health_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/api/health"


def is_server_healthy(host: str, port: int, *, timeout: float = 0.5) -> bool:
    try:
        resp = httpx.get(health_url(host, port), timeout=timeout)
        return resp.is_success
    except httpx.HTTPError:
        return False


def wait_for_health(host: str, port: int, *, timeout: float = HEALTH_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(health_url(host, port), timeout=0.5)
            if resp.is_success:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(HEALTH_POLL_S)
    detail = f" ({last_error})" if last_error else ""
    raise TimeoutError(
        f"PaperlessAgent server did not become ready at {health_url(host, port)}{detail}"
    )


def _start_uvicorn(host: str, port: int) -> uvicorn.Server:
    # Import app only after DATA_DIR / env are prepared so config picks them up.
    from app.main import app

    uv_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=os.getenv("PAPERLESS_LOG_LEVEL", "warning"),
        access_log=False,
    )
    server = uvicorn.Server(uv_config)

    def _run() -> None:
        # uvicorn.Server.run() installs its own signal handlers; disable in thread.
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        server.run()

    thread = threading.Thread(target=_run, name="paperlessagent-uvicorn", daemon=True)
    thread.start()
    return server


def _stop_uvicorn(server: uvicorn.Server) -> None:
    server.should_exit = True


def run_desktop(
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> int:
    """
    Open the PaperlessAgent UI in a native window.

    Starts a local uvicorn server when nothing healthy is already listening.
    """
    _prepare_environment()
    root = _project_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    preferred = port
    if preferred is None:
        env_port = os.getenv("PAPERLESS_PORT", "").strip()
        preferred = int(env_port) if env_port.isdigit() else None

    assert_bind_allowed(host)

    # Prefer an already-running local instance (e.g. systemd --user service).
    reuse_port = preferred or 8080
    owned_server: uvicorn.Server | None = None
    if is_server_healthy(host, reuse_port):
        active_port = reuse_port
    else:
        active_port = _pick_port(host, preferred)
        owned_server = _start_uvicorn(host, active_port)
        try:
            wait_for_health(host, active_port)
        except TimeoutError:
            if owned_server is not None:
                _stop_uvicorn(owned_server)
            raise

    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "pywebview is required for the desktop window. "
            "Install with: pip install -e '.[desktop]' -c constraints.txt "
            "(or: pip install -r requirements-desktop.txt). "
            "Also needs WebKitGTK on Linux "
            "(gir1.2-webkit2-4.1 or gir1.2-webkit2-4.0)."
        ) from exc

    url = f"http://{host}:{active_port}/"
    webview.create_window(
        "PaperlessAgent",
        url,
        width=width,
        height=height,
        min_size=(900, 600),
    )
    try:
        webview.start()
    finally:
        if owned_server is not None:
            _stop_uvicorn(owned_server)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PaperlessAgent desktop window")
    parser.add_argument(
        "--host",
        default=os.getenv("PAPERLESS_HOST", DEFAULT_HOST),
        help="Loopback host for the embedded server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to use (default: PAPERLESS_PORT, or 8080 if free / ephemeral)",
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    args = parser.parse_args(argv)
    return run_desktop(host=args.host, port=args.port, width=args.width, height=args.height)


if __name__ == "__main__":
    raise SystemExit(main())
