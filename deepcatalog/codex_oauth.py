"""ChatGPT / Codex OAuth (PKCE) login for DeepCatalog."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape as html_escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from deepcatalog.auth import codex_home

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 1455
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"
SCOPE = "openid profile email offline_access"
ORIGINATOR = "deepcatalog"
CODEX_RESPONSES_BASE_URL = "https://chatgpt.com/backend-api/codex"
TOKEN_REFRESH_SKEW_SECONDS = 60


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def decode_chatgpt_identity(access_token: str) -> dict[str, str | None]:
    """Best-effort decode of ChatGPT claims from the access-token JWT."""
    empty: dict[str, str | None] = {
        "account_id": None,
        "email": None,
        "plan_type": None,
    }
    parts = access_token.split(".")
    if len(parts) != 3:
        return empty
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return empty
    auth = payload.get("https://api.openai.com/auth") or {}
    profile = payload.get("https://api.openai.com/profile") or {}
    return {
        "account_id": auth.get("chatgpt_account_id"),
        "email": profile.get("email"),
        "plan_type": auth.get("chatgpt_plan_type"),
    }


@dataclass
class OAuthSession:
    state: str
    verifier: str
    authorize_url: str
    created_at: float = field(default_factory=time.time)
    completed: bool = False
    error: str | None = None
    result: dict[str, Any] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)


_sessions: dict[str, OAuthSession] = {}
_sessions_lock = threading.Lock()
_callback_server: HTTPServer | None = None
_callback_thread: threading.Thread | None = None


def _auth_path() -> Any:
    return codex_home() / "auth.json"


def save_chatgpt_tokens(
    *,
    access_token: str,
    refresh_token: str,
    expires_in: int | None = None,
    id_token: str | None = None,
) -> dict[str, Any]:
    """Persist tokens in a Codex-compatible ~/.codex/auth.json."""
    identity = decode_chatgpt_identity(access_token)
    account_id = identity.get("account_id")
    if not account_id:
        raise RuntimeError("ChatGPT access token is missing chatgpt_account_id")

    now = datetime.now(timezone.utc)
    expires_at = None
    if expires_in is not None:
        expires_at = now.timestamp() + int(expires_in)

    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    existing = {}
    path = _auth_path()
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}

    payload = {
        **existing,
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": id_token,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
        },
        "last_refresh": now.isoformat(),
        "expires_at": expires_at,
        "email": identity.get("email"),
        "plan_type": identity.get("plan_type"),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return {
        "status": "success",
        "auth_mode": "chatgpt",
        "account_id": account_id,
        "email": identity.get("email"),
        "plan_type": identity.get("plan_type"),
        "path": str(path),
    }


def save_api_key(api_key: str) -> dict[str, Any]:
    """Persist a Platform API key into ~/.codex/auth.json."""
    key = api_key.strip()
    if not key.startswith("sk-"):
        raise ValueError("API key should start with sk-")
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    path = _auth_path()
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    payload = {
        **existing,
        "auth_mode": "api",
        "OPENAI_API_KEY": key,
        "tokens": None,
        "last_refresh": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return {"status": "success", "auth_mode": "api", "path": str(path)}


def clear_auth() -> dict[str, Any]:
    """Remove stored Codex/OpenAI credentials."""
    path = _auth_path()
    if path.is_file():
        path.unlink()
    return {"status": "success", "cleared": True}


def exchange_authorization_code(*, code: str, verifier: str) -> dict[str, Any]:
    """Exchange an auth code + PKCE verifier for tokens."""
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Token exchange failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        raise RuntimeError("Token response missing access_token or refresh_token")
    return save_chatgpt_tokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=data.get("expires_in"),
        id_token=data.get("id_token"),
    )


def refresh_chatgpt_tokens(refresh_token: str) -> dict[str, Any]:
    """Refresh ChatGPT OAuth tokens and rewrite auth.json."""
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Token refresh failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    access = data.get("access_token")
    refresh = data.get("refresh_token") or refresh_token
    if not access:
        raise RuntimeError("Refresh response missing access_token")
    return save_chatgpt_tokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=data.get("expires_in"),
        id_token=data.get("id_token"),
    )


def read_stored_chatgpt_tokens() -> dict[str, Any] | None:
    path = _auth_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return None
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    account_id = tokens.get("account_id")
    if not access or not refresh:
        return None
    if not account_id:
        account_id = decode_chatgpt_identity(access).get("account_id")
    if not account_id:
        return None
    return {
        "access_token": access,
        "refresh_token": refresh,
        "account_id": account_id,
        "expires_at": data.get("expires_at"),
        "email": data.get("email"),
        "plan_type": data.get("plan_type"),
        "id_token": tokens.get("id_token"),
    }


def get_valid_chatgpt_tokens() -> dict[str, Any] | None:
    """Return ChatGPT tokens, refreshing when near expiry."""
    tokens = read_stored_chatgpt_tokens()
    if not tokens:
        return None
    expires_at = tokens.get("expires_at")
    if isinstance(expires_at, (int, float)):
        if time.time() >= float(expires_at) - TOKEN_REFRESH_SKEW_SECONDS:
            refresh_chatgpt_tokens(tokens["refresh_token"])
            return read_stored_chatgpt_tokens()
    return tokens


def parse_manual_callback(raw: str) -> tuple[str | None, str | None]:
    """Parse a pasted callback URL, query string, or bare code."""
    text = raw.strip()
    if not text:
        return None, None
    if text.startswith("http://") or text.startswith("https://"):
        try:
            url = urlparse(text)
            params = parse_qs(url.query)
            code_vals = params.get("code") or []
            state_vals = params.get("state") or []
            code = code_vals[0] if code_vals else None
            state = state_vals[0] if state_vals else None
            return code, state
        except ValueError:
            return None, None
    if "code=" in text:
        params = parse_qs(text.lstrip("?"))
        code_vals = params.get("code") or []
        state_vals = params.get("state") or []
        return (
            code_vals[0] if code_vals else None,
            state_vals[0] if state_vals else None,
        )
    return text, None


def _ensure_callback_server() -> None:
    """Listen on localhost:1455 for OAuth redirects (Codex-compatible)."""
    global _callback_server, _callback_thread
    if _callback_server is not None:
        return

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            code_vals = params.get("code") or []
            state_vals = params.get("state") or []
            error_vals = params.get("error") or []
            code = code_vals[0] if code_vals else None
            state = state_vals[0] if state_vals else None
            error = error_vals[0] if error_vals else None
            html = (
                "<!doctype html><html><body style='font-family:sans-serif;padding:2rem'>"
                "<h2>DeepCatalog</h2><p>Sign-in complete. You can close this tab "
                "and return to the app.</p></body></html>"
            )
            if error:
                html = (
                    "<!doctype html><html><body style='font-family:sans-serif;padding:2rem'>"
                    f"<h2>Sign-in failed</h2><p>{html_escape(error)}</p></body></html>"
                )
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
                _complete_session(state, error=error)
                return
            if not code or not state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code/state")
                return
            try:
                _complete_session(state, code=code)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(str(exc).encode("utf-8"))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    try:
        server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)
    except OSError as exc:
        # Port busy — UI can still complete via paste.
        raise RuntimeError(
            f"Could not bind OAuth callback on {CALLBACK_HOST}:{CALLBACK_PORT}: {exc}"
        ) from exc

    _callback_server = server
    _callback_thread = threading.Thread(target=server.serve_forever, daemon=True)
    _callback_thread.start()


def _complete_session(
    state: str | None,
    *,
    code: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    if not state:
        raise RuntimeError("Missing OAuth state")
    with _sessions_lock:
        session = _sessions.get(state)
    if session is None:
        raise RuntimeError("Unknown or expired OAuth session")
    with session._lock:
        if session.completed:
            return session.result
        if error:
            session.completed = True
            session.error = error
            return None
        if not code:
            raise RuntimeError("Missing authorization code")
        result = exchange_authorization_code(code=code, verifier=session.verifier)
        session.completed = True
        session.result = result
        return result


def start_oauth_login() -> dict[str, Any]:
    """Start a PKCE login and return the authorize URL for the browser."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_hex(16)
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "originator": ORIGINATOR,
    }
    authorize_url = f"{AUTHORIZE_URL}?{urlencode(params)}"
    session = OAuthSession(
        state=state,
        verifier=verifier,
        authorize_url=authorize_url,
    )
    with _sessions_lock:
        # Drop stale sessions (>15 min)
        cutoff = time.time() - 900
        for key in list(_sessions.keys()):
            if _sessions[key].created_at < cutoff:
                del _sessions[key]
        _sessions[state] = session

    callback_ready = True
    callback_error = None
    try:
        _ensure_callback_server()
    except RuntimeError as exc:
        callback_ready = False
        callback_error = str(exc)

    return {
        "status": "success",
        "state": state,
        "authorize_url": authorize_url,
        "redirect_uri": REDIRECT_URI,
        "callback_ready": callback_ready,
        "callback_error": callback_error,
        "hint": (
            "Open the authorize URL, sign in with ChatGPT, then return here. "
            "If the browser does not redirect automatically, paste the "
            "callback URL into the app."
        ),
    }


def poll_oauth_login(state: str) -> dict[str, Any]:
    with _sessions_lock:
        session = _sessions.get(state)
    if session is None:
        return {"status": "error", "error": "unknown_state"}
    with session._lock:
        if session.error:
            return {"status": "error", "error": session.error}
        if session.completed and session.result:
            return {"status": "success", **session.result}
    return {"status": "pending"}


def complete_oauth_login_manual(*, state: str, raw: str) -> dict[str, Any]:
    code, pasted_state = parse_manual_callback(raw)
    if pasted_state and pasted_state != state:
        raise RuntimeError("State mismatch — paste the URL from this login attempt")
    if not code:
        raise RuntimeError("Could not find an authorization code in that input")
    result = _complete_session(state, code=code)
    if not result:
        raise RuntimeError("OAuth completion failed")
    return result
