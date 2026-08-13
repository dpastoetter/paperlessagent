"""Local API security: bind policy, bearer/session token, Host allowlist."""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets

TOKEN_ENV = "PAPERLESS_API_TOKEN"
ALLOWED_HOSTS_ENV = "PAPERLESS_ALLOWED_HOSTS"
COOKIE_NAME = "pa_session"
AUTH_HEADER = "Authorization"

# Hosts treated as loopback for bind checks and client trust.
_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "::1", "localhost", "testclient", "testserver"})


def is_loopback_hostname(host: str | None) -> bool:
    """True for loopback hostnames / IPs (ignoring optional port)."""
    if not host:
        return False
    name = host.strip().lower()
    if name.startswith("[") and "]" in name:
        name = name[1 : name.index("]")]
    elif name.count(":") == 1 and not name.startswith(":"):
        # hostname:port
        name = name.rsplit(":", 1)[0]
    if name in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def is_wildcard_or_non_loopback_bind(host: str | None) -> bool:
    """True when binding would accept connections from the network."""
    if not host:
        return False
    name = host.strip().lower()
    if name in {"0.0.0.0", "::", "[::]", "*"}:
        return True
    if is_loopback_hostname(name):
        return False
    try:
        # Specific interface IP (LAN/WAN) — also non-loopback.
        return not ipaddress.ip_address(name).is_loopback
    except ValueError:
        # Unknown hostname — treat as non-loopback to be safe.
        return name not in _LOOPBACK_HOSTNAMES


def get_api_token() -> str | None:
    """Return the configured API token, or None if unset."""
    raw = os.getenv(TOKEN_ENV, "").strip()
    return raw or None


def generate_api_token() -> str:
    """Cryptographically strong token for PAPERLESS_API_TOKEN."""
    return secrets.token_urlsafe(32)


def assert_bind_allowed(host: str | None) -> None:
    """
    Refuse non-loopback binds unless PAPERLESS_API_TOKEN is configured.

    Call this before starting uvicorn (desktop, systemd, or documented CLI).
    """
    if not is_wildcard_or_non_loopback_bind(host):
        return
    if get_api_token():
        return
    raise RuntimeError(
        f"Refusing to bind to {host!r} without {TOKEN_ENV}. "
        "The API has no user login; exposing it on the network requires a shared "
        f'secret. Generate one with: python -c "from paperless_agent.local_security '
        f'import generate_api_token; print(generate_api_token())" '
        f"then set {TOKEN_ENV}=… in .env (and keep PAPERLESS_HOST loopback when possible)."
    )


def auth_required_for_request(*, client_host: str | None) -> bool:
    """Bearer/cookie auth is required when a token is configured or the peer is remote."""
    if get_api_token():
        return True
    return not is_loopback_hostname(client_host)


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def token_matches(candidate: str | None, expected: str | None) -> bool:
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate, expected)


def allowed_hosts() -> set[str]:
    """Hostnames allowed in the Host header (without ports)."""
    raw = os.getenv(ALLOWED_HOSTS_ENV, "").strip()
    if raw:
        hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    else:
        hosts = {"localhost", "127.0.0.1", "::1", "testclient"}
    # Always allow loopback names even if the env list is customized.
    hosts.update(_LOOPBACK_HOSTNAMES)
    # Include the configured bind host when it is a concrete hostname/IP.
    bind = (os.getenv("PAPERLESS_HOST") or "").strip().lower()
    if bind and bind not in {"0.0.0.0", "::", "[::]", "*"}:
        hosts.add(bind.strip("[]"))
    return hosts


def host_header_allowed(host_header: str | None) -> bool:
    """Validate Host / :authority against the allowlist (DNS-rebinding hardening)."""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("[") and "]" in host:
        name = host[1 : host.index("]")]
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if host.count(":") > 1:
            # bare IPv6 without brackets
            name = host
    if name in allowed_hosts():
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False
