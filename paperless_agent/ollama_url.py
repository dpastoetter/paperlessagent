"""Validate Ollama base URLs to prevent SSRF and accidental remote processing."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from paperless_agent.local_security import env_flag, is_loopback_hostname

ALLOW_REMOTE_OLLAMA_ENV = "PAPERLESS_ALLOW_REMOTE_OLLAMA"
DEFAULT_LOCAL_OLLAMA_URL = "http://localhost:11434"

# Hostnames commonly used for cloud instance metadata (resolve to link-local).
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)


def allow_remote_ollama_enabled() -> bool:
    """Explicit env opt-in for non-loopback Ollama (LAN / remote host)."""
    return env_flag(ALLOW_REMOTE_OLLAMA_ENV)


def normalize_ollama_base_url(url: str | None) -> str:
    raw = (url or "").strip() or DEFAULT_LOCAL_OLLAMA_URL
    return raw.rstrip("/")


def is_loopback_ollama_url(url: str) -> bool:
    """True when the URL host is loopback by name (before DNS)."""
    parsed = urlparse(normalize_ollama_base_url(url))
    host = parsed.hostname
    return bool(host) and is_loopback_hostname(host)


def _reject_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_remote: bool
) -> str | None:
    if ip.is_unspecified:
        return "unspecified address"
    if ip.is_multicast:
        return "multicast address"
    if ip.is_link_local:
        return "link-local / metadata address"
    if ip.is_reserved and not ip.is_loopback:
        return "reserved address"
    if ip.is_loopback:
        return None
    if not allow_remote:
        return "non-loopback address (local Ollama allows localhost / 127.0.0.1 / ::1 only)"
    return None


def _resolve_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve Ollama host {host!r}: {exc}") from exc
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr = str(sockaddr[0])
        if addr in seen:
            continue
        seen.add(addr)
        try:
            ips.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    if not ips:
        raise ValueError(f"Cannot resolve Ollama host {host!r} to an IP address")
    return ips


def validate_ollama_base_url(url: str | None, *, allow_remote: bool = False) -> str:
    """
    Normalize and validate an Ollama base URL.

    Local mode (``allow_remote=False``): host must be loopback after DNS resolution.
    Remote mode: http(s) only; rejects link-local/metadata and other unsafe targets.
    """
    normalized = normalize_ollama_base_url(url)
    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Ollama base URL must use http:// or https://")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Ollama base URL must not include credentials")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Ollama base URL must be an origin only (no path, query, or fragment)")
    host = parsed.hostname
    if not host:
        raise ValueError("Ollama base URL must include a host")
    if host.strip(".").lower() in _BLOCKED_HOSTNAMES:
        raise ValueError(f"Ollama host {host!r} is not allowed")

    # Literal IPs and hostnames both go through resolution so DNS rebinding is checked.
    ips = _resolve_host_ips(host)
    for ip in ips:
        reason = _reject_ip(ip, allow_remote=allow_remote)
        if reason:
            raise ValueError(f"Ollama base URL resolves to a blocked address ({ip}): {reason}")

    # Rebuild without trailing junk; preserve brackets for IPv6 literals.
    port = parsed.port
    if ":" in host and not host.startswith("["):
        host_part = f"[{host}]"
    else:
        host_part = host
    netloc = f"{host_part}:{port}" if port else host_part
    return f"{parsed.scheme}://{netloc}"


def require_ollama_base_url(url: str | None, *, allow_remote: bool = False) -> str:
    """
    Validate a URL for outbound Ollama HTTP.

    Remote destinations also require ``PAPERLESS_ALLOW_REMOTE_OLLAMA`` or an
    explicit ``allow_remote=True`` from an authenticated API that already checked
    the privacy disclaimer.
    """
    normalized = normalize_ollama_base_url(url)
    remote = not is_loopback_ollama_url(normalized)
    if remote and not (allow_remote or allow_remote_ollama_enabled()):
        raise ValueError(
            "Remote Ollama is disabled. Use localhost / 127.0.0.1 / ::1, or enable "
            "Remote Ollama (allow_remote=true and approve the privacy disclaimer), "
            f"or set {ALLOW_REMOTE_OLLAMA_ENV}=1 for a configured remote server."
        )
    return validate_ollama_base_url(normalized, allow_remote=remote)


def remote_ollama_allowed_for_request(*, allow_remote: bool) -> bool:
    """Whether this API call may target a non-loopback Ollama URL."""
    return bool(allow_remote) or allow_remote_ollama_enabled()
