"""Local API security: bind policy, sessions token, Host allowlist, trusted proxies."""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path

TOKEN_ENV = "PAPERLESS_API_TOKEN"
ALLOW_REMOTE_ENV = "PAPERLESS_ALLOW_REMOTE"
ALLOWED_HOSTS_ENV = "PAPERLESS_ALLOWED_HOSTS"
TRUSTED_PROXIES_ENV = "PAPERLESS_TRUSTED_PROXIES"
SSL_CERTFILE_ENV = "PAPERLESS_SSL_CERTFILE"
SSL_KEYFILE_ENV = "PAPERLESS_SSL_KEYFILE"
COOKIE_NAME = "pa_session"
AUTH_HEADER = "Authorization"

_Network = IPv4Network | IPv6Network

# Hosts treated as loopback for bind checks and client trust.
_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "::1", "localhost", "testclient", "testserver"})


def env_flag(name: str) -> bool:
    """True when an env var is a common truthy string (1/true/yes/on)."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


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


def allow_remote_enabled() -> bool:
    """Explicit opt-in for non-loopback binds (network mode)."""
    return env_flag(ALLOW_REMOTE_ENV)


def ssl_cert_paths() -> tuple[Path, Path] | None:
    """Return (certfile, keyfile) when both PAPERLESS_SSL_* paths are set and exist."""
    cert = os.getenv(SSL_CERTFILE_ENV, "").strip()
    key = os.getenv(SSL_KEYFILE_ENV, "").strip()
    if not cert or not key:
        return None
    cert_path = Path(cert).expanduser()
    key_path = Path(key).expanduser()
    if not cert_path.is_file() or not key_path.is_file():
        return None
    return cert_path.resolve(), key_path.resolve()


def ssl_configured() -> bool:
    return ssl_cert_paths() is not None


def _parse_networks(raw: str) -> list[_Network]:
    nets: list[_Network] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            if "/" in item:
                net = ipaddress.ip_network(item, strict=False)
            else:
                addr = ipaddress.ip_address(item)
                net = ipaddress.ip_network(f"{addr}/{addr.max_prefixlen}", strict=False)
            if isinstance(net, (IPv4Network, IPv6Network)):
                nets.append(net)
        except ValueError:
            continue
    return nets


def trusted_proxy_networks() -> list[_Network]:
    """CIDRs/IPs allowed to set X-Forwarded-* (empty = never trust proxy headers)."""
    return _parse_networks(os.getenv(TRUSTED_PROXIES_ENV, ""))


def is_trusted_proxy(host: str | None) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host.strip())
    except ValueError:
        return False
    return any(addr in net for net in trusted_proxy_networks())


def assert_bind_allowed(host: str | None) -> None:
    """
    Enforce local vs network bind policy.

    - Local mode (loopback): plain HTTP is fine.
    - Network mode (0.0.0.0 / LAN IP / …): requires PAPERLESS_ALLOW_REMOTE=1,
      PAPERLESS_API_TOKEN, and TLS cert/key files on the process itself.

    Preferred LAN exposure: keep PAPERLESS_HOST=127.0.0.1 and terminate TLS on a
    reverse proxy (Caddy/nginx/Traefik). That stays in local mode for the app.
    """
    if not is_wildcard_or_non_loopback_bind(host):
        return

    problems: list[str] = []
    if not allow_remote_enabled():
        problems.append(
            f"set {ALLOW_REMOTE_ENV}=1 to acknowledge network exposure "
            "(plain HTTP on a LAN is not enough — see README network mode)"
        )
    if not get_api_token():
        problems.append(
            f"set {TOKEN_ENV}=… "
            f'(python -c "from paperless_agent.local_security import generate_api_token; '
            f'print(generate_api_token())")'
        )
    if not ssl_configured():
        problems.append(
            f"set {SSL_CERTFILE_ENV} and {SSL_KEYFILE_ENV} to existing PEM files "
            "(network mode requires HTTPS on the uvicorn process), "
            "or keep PAPERLESS_HOST=127.0.0.1 and put Caddy/nginx/Traefik in front"
        )
    if not problems:
        return
    detail = "; ".join(problems)
    raise RuntimeError(
        f"Refusing to bind to {host!r} in network mode — {detail}. "
        "Local mode (127.0.0.1 / ::1) allows HTTP without these flags."
    )


def is_direct_loopback_request(*, peer_host: str | None, host_header: str | None) -> bool:
    """True only for a browser hitting the app directly on loopback.

    Requires a loopback TCP peer *and* a loopback Host header. A peer listed in
    PAPERLESS_TRUSTED_PROXIES is the reverse-proxy hop, not the user — even when
    that hop is 127.0.0.1. X-Forwarded-* is ignored (never an auth signal).
    """
    if not peer_host or not host_header:
        return False
    if is_trusted_proxy(peer_host):
        return False
    return is_loopback_hostname(peer_host) and is_loopback_hostname(host_header)


def auth_required_for_request(*, peer_host: str | None, host_header: str | None = None) -> bool:
    """Bearer/cookie auth is required unless this is a genuine direct loopback client.

    A configured API token always requires auth. X-Forwarded-* is never used here.
    """
    if get_api_token():
        return True
    return not is_direct_loopback_request(peer_host=peer_host, host_header=host_header)


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


def forwarded_client_host(
    *,
    peer_host: str | None,
    x_forwarded_for: str | None,
) -> str | None:
    """
    Resolve the browser/client host for logging / HTTPS detection.

    X-Forwarded-For is only honored when the immediate TCP peer is in
    PAPERLESS_TRUSTED_PROXIES. The chain is walked right-to-left: trusted
    proxies at the near end are discarded and the first untrusted hop is
    the client. This value must never be used to grant authentication.
    """
    if not is_trusted_proxy(peer_host):
        return peer_host
    if not x_forwarded_for:
        return peer_host
    hops = [part.strip() for part in x_forwarded_for.split(",") if part.strip()]
    for candidate in reversed(hops):
        if is_trusted_proxy(candidate):
            continue
        return candidate
    return peer_host


def request_appears_https(
    *,
    peer_host: str | None,
    url_scheme: str | None,
    x_forwarded_proto: str | None,
) -> bool:
    """True for direct HTTPS or HTTPS reported by a trusted proxy only."""
    if (url_scheme or "").lower() == "https":
        return True
    if not is_trusted_proxy(peer_host):
        return False
    if not x_forwarded_proto:
        return False
    proto = x_forwarded_proto.split(",")[-1].strip().lower()
    return proto == "https"
