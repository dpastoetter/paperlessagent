"""Native desktop window shell around the local FastAPI UI (pywebview)."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv

from deepcatalog.env_permissions import harden_secret_file, write_secret_text
from deepcatalog.local_security import (
    assert_bind_allowed,
    port_probe_host,
    ssl_cert_paths,
    sync_configured_bind,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 840
HEALTH_TIMEOUT_S = 30.0
HEALTH_POLL_S = 0.15
WM_CLASS = "DeepCatalog"
DESKTOP_FILE_NAME = "deepcatalog.desktop"
ICON_THEME_NAME = "deepcatalog"
CHROMIUM_HANDOFF_S = 2.5
CHROMIUM_BINARIES = (
    "brave-browser",
    "brave-browser-stable",
    "brave",
    "google-chrome-stable",
    "google-chrome",
    "chromium-browser",
    "chromium",
    "microsoft-edge-stable",
    "microsoft-edge",
    "vivaldi-stable",
    "vivaldi",
)
CHROMIUM_FALLBACK_PATHS = (
    "/opt/brave.com/brave/brave",
    "/opt/google/chrome/chrome",
    "/usr/lib64/chromium-browser/chromium-browser",
    "/usr/lib/chromium-browser/chromium-browser",
)

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    override = os.getenv("DEEPCATALOG_PROJECT_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "deepcatalog"


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
        write_secret_text(env_file, example.read_text(encoding="utf-8"))
    elif env_file.exists():
        harden_secret_file(env_file, fix=True)
    if env_file.exists():
        load_dotenv(env_file, override=False)
    return data_dir


def _port_is_free(host: str, port: int) -> bool:
    probe = port_probe_host(host)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((probe, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, preferred: int | None) -> int:
    if preferred and preferred > 0 and _port_is_free(host, preferred):
        return preferred
    if _port_is_free(host, 8080):
        return 8080
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((port_probe_host(host), 0))
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
        f"DeepCatalog server did not become ready at {health_url(host, port)}{detail}"
    )


def _start_uvicorn(host: str, port: int) -> uvicorn.Server:
    # Import app only after DATA_DIR / env are prepared so config picks them up.
    from app.main import app

    sync_configured_bind(host, port)
    ssl_paths = ssl_cert_paths()
    uv_kwargs: dict = {
        "app": app,
        "host": host,
        "port": port,
        "log_level": os.getenv("DEEPCATALOG_LOG_LEVEL", "warning"),
        "access_log": False,
    }
    if ssl_paths:
        cert, key = ssl_paths
        uv_kwargs["ssl_certfile"] = str(cert)
        uv_kwargs["ssl_keyfile"] = str(key)
    uv_config = uvicorn.Config(**uv_kwargs)
    server = uvicorn.Server(uv_config)

    def _run() -> None:
        # uvicorn.Server.run() installs its own signal handlers; disable in thread.
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        server.run()

    thread = threading.Thread(target=_run, name="deepcatalog-uvicorn", daemon=True)
    thread.start()
    return server


def _stop_uvicorn(server: uvicorn.Server) -> None:
    server.should_exit = True


def _wait_for_server(server: uvicorn.Server) -> None:
    """Block until SIGINT/SIGTERM or the embedded uvicorn loop exits."""

    def _stop(_signum: int | None = None, _frame: object = None) -> None:
        server.should_exit = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while not server.should_exit:
        time.sleep(0.25)


def xdg_data_home() -> Path:
    raw = os.getenv("XDG_DATA_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".local" / "share"


def window_icon_path() -> Path | None:
    """PNG preferred (GTK window icon), then SVG. AppImage overlay first."""
    names = ("deepcatalog.png", "deepcatalog.svg")
    candidates: list[Path] = []
    appdir = os.getenv("APPDIR", "").strip()
    if appdir:
        root = Path(appdir)
        for name in names:
            candidates.append(root / name)
            candidates.append(root / "usr/share/icons/hicolor/256x256/apps" / name)
            candidates.append(root / "usr/share/icons/hicolor/scalable/apps" / name)
    project = _project_root()
    for name in names:
        candidates.append(project / "packaging" / "linux" / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _quote_desktop_exec_arg(value: str) -> str:
    if value and all(ch.isalnum() or ch in "/._-+:@" for ch in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def desktop_exec_command() -> str:
    """Exec= line for the user .desktop file (AppImage path, or this interpreter)."""
    image = os.getenv("APPIMAGE", "").strip()
    if image and Path(image).is_file():
        return _quote_desktop_exec_arg(str(Path(image).resolve()))
    return " ".join(
        [
            _quote_desktop_exec_arg(sys.executable),
            "-m",
            "deepcatalog.desktop",
        ]
    )


def render_desktop_entry(*, exec_line: str, icon: str = ICON_THEME_NAME) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.1\n"
        "Name=DeepCatalog Studio\n"
        "Comment=Your local workspace for archive discovery and automation.\n"
        f"Exec={exec_line}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Office;Scanning;Utility;\n"
        "StartupNotify=true\n"
        f"StartupWMClass={WM_CLASS}\n"
        "MimeType=application/pdf;image/png;image/jpeg;image/tiff;image/webp;\n"
        "Keywords=OCR;PDF;documents;archive;RAG;\n"
    )


def _copy_icon_into_hicolor(src: Path, data_home: Path) -> Path | None:
    name = src.name.lower()
    if name.endswith(".png"):
        dest = data_home / "icons" / "hicolor" / "256x256" / "apps" / "deepcatalog.png"
    elif name.endswith(".svg"):
        dest = data_home / "icons" / "hicolor" / "scalable" / "apps" / "deepcatalog.svg"
    else:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def install_linux_desktop_entry() -> Path | None:
    """Install ~/.local/share/applications/deepcatalog.desktop and themed icons."""
    if not sys.platform.startswith("linux"):
        return None
    data_home = xdg_data_home()
    applications = data_home / "applications"
    applications.mkdir(parents=True, exist_ok=True)

    icon_field = ICON_THEME_NAME
    png = None
    svg = None
    icon_src = window_icon_path()
    appdir = os.getenv("APPDIR", "").strip()
    search: list[Path] = []
    if icon_src is not None:
        search.append(icon_src)
    if appdir:
        search.extend(
            [
                Path(appdir) / "deepcatalog.png",
                Path(appdir) / "deepcatalog.svg",
                Path(appdir) / "usr/share/icons/hicolor/256x256/apps/deepcatalog.png",
                Path(appdir) / "usr/share/icons/hicolor/scalable/apps/deepcatalog.svg",
            ]
        )
    project_packaging = _project_root() / "packaging" / "linux"
    search.extend(
        [
            project_packaging / "deepcatalog.png",
            project_packaging / "deepcatalog.svg",
        ]
    )
    seen: set[Path] = set()
    for src in search:
        resolved = src.resolve() if src.exists() else src
        if resolved in seen or not src.is_file():
            continue
        seen.add(resolved)
        copied = _copy_icon_into_hicolor(src, data_home)
        if copied is None:
            continue
        if copied.suffix == ".png" and png is None:
            png = copied
        if copied.suffix == ".svg" and svg is None:
            svg = copied
    if png is not None:
        icon_field = str(png)
    elif svg is not None:
        icon_field = str(svg)

    dest = applications / DESKTOP_FILE_NAME
    text = render_desktop_entry(exec_line=desktop_exec_command(), icon=icon_field)
    if dest.is_file() and dest.read_text(encoding="utf-8") == text:
        return dest
    dest.write_text(text, encoding="utf-8")
    try:
        dest.chmod(0o644)
    except OSError:
        pass
    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run(  # noqa: S603
            [updater, str(applications)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    logger.info("Installed desktop entry %s", dest)
    return dest


def desktop_ui_url(host: str, port: int) -> str:
    """Local UI URL with desktop=1 so the SPA uses app chrome, not website chrome."""
    return f"http://{host}:{port}/?desktop=1"


def prefer_chromium_app_window() -> bool:
    """AppImage Python has no PyGObject — Chromium --app is the real window."""
    if os.getenv("APPIMAGE", "").strip():
        return True
    return os.getenv("DEEPCATALOG_APPIMAGE", "").strip().lower() in {"1", "true", "yes"}


def find_chromium_app_browser() -> str | None:
    for name in CHROMIUM_BINARIES:
        found = shutil.which(name)
        if found:
            return found
    for raw in CHROMIUM_FALLBACK_PATHS:
        candidate = Path(raw)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def chromium_profile_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "chromium-profile"


def chromium_app_argv(
    browser: str,
    url: str,
    profile: Path,
    *,
    width: int,
    height: int,
) -> list[str]:
    profile.mkdir(parents=True, exist_ok=True)
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        f"--class={WM_CLASS}",
        f"--name={WM_CLASS}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-features=TranslateUI,MediaRouter,InfiniteSessionRestore",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--password-store=basic",
        "--noerrdialogs",
        # XWayland so --class/StartupWMClass apply; native Wayland groups with Brave.
        "--ozone-platform-hint=x11",
    ]


def open_chromium_app_window(
    url: str,
    *,
    data_dir: Path,
    width: int,
    height: int,
) -> str | None:
    """Open a chromeless Chromium window. Returns closed / detached, or None."""
    browser = find_chromium_app_browser()
    if not browser:
        logger.info("No Chromium-based browser found for an --app window")
        return None
    profile = chromium_profile_dir(data_dir)
    argv = chromium_app_argv(browser, url, profile, width=width, height=height)
    env = os.environ.copy()
    env["CHROME_DESKTOP"] = DESKTOP_FILE_NAME
    logger.info("Opening app window with %s", browser)
    try:
        proc = subprocess.Popen(  # noqa: S603
            argv,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning("Could not launch %s: %s", browser, exc)
        return None
    try:
        proc.wait(timeout=CHROMIUM_HANDOFF_S)
    except subprocess.TimeoutExpired:
        proc.wait()
        return "closed"
    if proc.returncode == 0:
        logger.info("Chromium --app handed off; keeping the local server running")
        return "detached"
    logger.warning("%s exited immediately with code %s", browser, proc.returncode)
    return None


def _open_in_browser(url: str) -> None:
    opener = shutil.which("xdg-open") or shutil.which("gio")
    if opener:
        subprocess.Popen(  # noqa: S603
            [opener, url] if opener.endswith("xdg-open") else [opener, "open", url],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    webbrowser.open(url)


def _apply_gtk_wm_class() -> None:
    """Set WM_CLASS before Gtk.Application starts.

    Optional host GI (PyGObject); not bundled in the AppImage Python.
    """
    try:
        from gi.repository import GLib
    except ImportError:
        return
    try:
        GLib.set_prgname(WM_CLASS)
        GLib.set_application_name("DeepCatalog Studio")
    except Exception as exc:  # noqa: BLE001 — GI bindings vary by distro
        logger.debug("Could not set GTK application id: %s", exc)


def _try_native_window(url: str, width: int, height: int) -> bool:
    """Open pywebview; return False when WebKitGTK / pywebview is unavailable."""
    try:
        import webview
    except ImportError as exc:
        logger.warning("pywebview is not available (%s)", exc)
        return False
    _apply_gtk_wm_class()
    icon = window_icon_path()
    try:
        window = webview.create_window(
            "DeepCatalog Studio",
            url,
            width=width,
            height=height,
            min_size=(900, 600),
            text_select=True,
        )
        if icon is not None and window is not None:
            events = getattr(window, "events", None)
            shown = getattr(events, "shown", None)

            def _apply_icon() -> None:
                native = getattr(window, "native", None)
                setter = getattr(native, "set_icon_from_file", None)
                if callable(setter):
                    try:
                        setter(str(icon))
                    except Exception as exc:  # noqa: BLE001 — icon is best-effort
                        logger.debug("Could not set native window icon: %s", exc)

            if shown is not None:
                shown += _apply_icon
        webview.start()
    except Exception:
        logger.exception(
            "Native WebKitGTK window failed; falling back to a Chromium --app window. "
            "Install WebKitGTK (webkit2gtk4.1) plus PyGObject for the GTK window."
        )
        return False
    return True


def _chromium_result_closes_ui(result: str | None) -> bool | None:
    """True = window closed; False = keep server; None = try the next backend."""
    if result == "closed":
        return True
    if result == "detached":
        return False
    return None


def _launch_ui_window(url: str, *, data_dir: Path, width: int, height: int) -> bool:
    """Open the UI. Return True when the window closed and the process should exit."""
    chromium_first = prefer_chromium_app_window()
    backends = (
        ("chromium-app", "webview", "browser")
        if chromium_first
        else ("webview", "chromium-app", "browser")
    )
    for backend in backends:
        if backend == "webview":
            if _try_native_window(url, width, height):
                return True
            continue
        if backend == "chromium-app":
            closed = _chromium_result_closes_ui(
                open_chromium_app_window(url, data_dir=data_dir, width=width, height=height)
            )
            if closed is None:
                continue
            return closed
        _open_in_browser(url)
        return False
    return False


def run_desktop(
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    headless: bool = False,
) -> int:
    """
    Open the DeepCatalog Studio UI in a native window.

    Starts a local uvicorn server when nothing healthy is already listening.
    ``headless`` keeps the server in the foreground (systemd / AppImage autostart).
    Window order: AppImage prefers a chromeless Chromium ``--app`` window
    (isolated profile, own WM_CLASS). Source installs try pywebview/WebKitGTK
    first, then Chromium ``--app``, then the default browser.
    """
    if not logging.getLogger().handlers:
        level_name = os.getenv("DEEPCATALOG_LOG_LEVEL", "warning").strip().upper() or "WARNING"
        level = getattr(logging, level_name, logging.WARNING)
        logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    data_dir = _prepare_environment()
    root = _project_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        install_linux_desktop_entry()
    except OSError:
        logger.exception("Could not install the DeepCatalog .desktop entry")

    preferred = port
    if preferred is None:
        env_port = os.getenv("DEEPCATALOG_PORT", "").strip()
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

    url = desktop_ui_url(host, active_port)
    if not headless:
        ui_closed = _launch_ui_window(url, data_dir=data_dir, width=width, height=height)
        if ui_closed:
            if owned_server is not None:
                _stop_uvicorn(owned_server)
            return 0

    if owned_server is None:
        return 0
    try:
        _wait_for_server(owned_server)
    finally:
        _stop_uvicorn(owned_server)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepCatalog Studio desktop window")
    parser.add_argument(
        "--host",
        default=os.getenv("DEEPCATALOG_HOST", DEFAULT_HOST),
        help="Loopback host for the embedded server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to use (default: DEEPCATALOG_PORT, or 8080 if free / ephemeral)",
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the local server without a native window (systemd / AppImage autostart)",
    )
    args = parser.parse_args(argv)
    return run_desktop(
        host=args.host,
        port=args.port,
        width=args.width,
        height=args.height,
        headless=args.headless,
    )


if __name__ == "__main__":
    raise SystemExit(main())
