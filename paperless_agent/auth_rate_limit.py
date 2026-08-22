"""Per-IP rate limits for token checks and session creation.

In-process sliding windows. Successful authenticated API calls are not counted.
Secrets are never logged.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Session exchange (POST /api/auth/session) — stricter than API 401s.
SESSION_ATTEMPT_LIMIT = 10
SESSION_ATTEMPT_WINDOW_S = 300.0
SESSION_FAILURE_LIMIT = 5
SESSION_FAILURE_WINDOW_S = 900.0
SESSION_LOCKOUT_S = 300.0

# Failed Bearer/cookie checks on protected API routes.
AUTH_FAILURE_LIMIT = 30
AUTH_FAILURE_WINDOW_S = 900.0
AUTH_LOCKOUT_S = 300.0

RATE_LIMIT_DETAIL = "too many authentication attempts — try again later"


class AuthRateLimiter:
    """Thread-safe per-IP sliding windows for auth attempts and failures."""

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.monotonic
        self._lock = threading.Lock()
        self._session_attempts: dict[str, list[float]] = {}
        self._session_failures: dict[str, list[float]] = {}
        self._session_lock_until: dict[str, float] = {}
        self._auth_failures: dict[str, list[float]] = {}
        self._auth_lock_until: dict[str, float] = {}

    def reset(self) -> None:
        with self._lock:
            self._session_attempts.clear()
            self._session_failures.clear()
            self._session_lock_until.clear()
            self._auth_failures.clear()
            self._auth_lock_until.clear()

    def check_session(self, ip: str) -> tuple[bool, float]:
        """Allow a session-exchange POST. Records the attempt when allowed."""
        key = _key(ip)
        with self._lock:
            now = self._now()
            retry = self._blocked(
                key,
                now,
                attempts=self._session_attempts,
                attempt_limit=SESSION_ATTEMPT_LIMIT,
                attempt_window=SESSION_ATTEMPT_WINDOW_S,
                failures=self._session_failures,
                failure_limit=SESSION_FAILURE_LIMIT,
                failure_window=SESSION_FAILURE_WINDOW_S,
                lock_until=self._session_lock_until,
            )
            if retry is not None:
                return False, retry
            self._session_attempts.setdefault(key, []).append(now)
            return True, 0.0

    def record_session_failure(self, ip: str) -> None:
        key = _key(ip)
        with self._lock:
            now = self._now()
            count = self._record_failure(
                key,
                now,
                self._session_failures,
                SESSION_FAILURE_WINDOW_S,
                self._session_lock_until,
                SESSION_FAILURE_LIMIT,
                SESSION_LOCKOUT_S,
            )
        _log_auth_failure(ip, "/api/auth/session", count)

    def record_session_success(self, ip: str) -> None:
        key = _key(ip)
        with self._lock:
            self._session_failures.pop(key, None)
            self._session_lock_until.pop(key, None)

    def check_api_auth(self, ip: str) -> tuple[bool, float]:
        """Allow a protected-API credential check (does not record on success)."""
        key = _key(ip)
        with self._lock:
            now = self._now()
            retry = self._blocked(
                key,
                now,
                attempts=None,
                attempt_limit=0,
                attempt_window=0.0,
                failures=self._auth_failures,
                failure_limit=AUTH_FAILURE_LIMIT,
                failure_window=AUTH_FAILURE_WINDOW_S,
                lock_until=self._auth_lock_until,
            )
            if retry is not None:
                return False, retry
            return True, 0.0

    def record_api_auth_failure(self, ip: str, path: str) -> None:
        key = _key(ip)
        with self._lock:
            now = self._now()
            count = self._record_failure(
                key,
                now,
                self._auth_failures,
                AUTH_FAILURE_WINDOW_S,
                self._auth_lock_until,
                AUTH_FAILURE_LIMIT,
                AUTH_LOCKOUT_S,
            )
        _log_auth_failure(ip, path, count)

    def _blocked(
        self,
        key: str,
        now: float,
        *,
        attempts: dict[str, list[float]] | None,
        attempt_limit: int,
        attempt_window: float,
        failures: dict[str, list[float]],
        failure_limit: int,
        failure_window: float,
        lock_until: dict[str, float],
    ) -> float | None:
        locked = lock_until.get(key, 0.0)
        if locked > now:
            return locked - now
        if attempts is not None:
            stamps = _prune(attempts.get(key, []), now, attempt_window)
            attempts[key] = stamps
            if len(stamps) >= attempt_limit:
                oldest = stamps[0]
                return max(1.0, oldest + attempt_window - now)
        fails = _prune(failures.get(key, []), now, failure_window)
        failures[key] = fails
        if len(fails) >= failure_limit:
            until = now + (SESSION_LOCKOUT_S if attempts is not None else AUTH_LOCKOUT_S)
            lock_until[key] = until
            return until - now
        return None

    def _record_failure(
        self,
        key: str,
        now: float,
        store: dict[str, list[float]],
        window: float,
        lock_until: dict[str, float],
        limit: int,
        lockout: float,
    ) -> int:
        stamps = _prune(store.get(key, []), now, window)
        stamps.append(now)
        store[key] = stamps
        if len(stamps) >= limit:
            lock_until[key] = now + lockout
        return len(stamps)


def _key(ip: str | None) -> str:
    raw = (ip or "").strip().lower()
    return raw or "unknown"


def _prune(stamps: list[float], now: float, window: float) -> list[float]:
    cutoff = now - window
    return [t for t in stamps if t > cutoff]


def _log_auth_failure(ip: str, path: str, failures: int) -> None:
    logger.warning(
        "Authentication failed ip=%s path=%s consecutive_window=%s",
        _key(ip),
        path,
        failures,
    )


def log_rate_limited(ip: str, path: str, retry_after: float) -> None:
    logger.warning(
        "Authentication rate-limited ip=%s path=%s retry_after=%s",
        _key(ip),
        path,
        max(1, int(retry_after)),
    )


def rate_limit_response_headers(retry_after: float) -> dict[str, str]:
    return {"Retry-After": str(max(1, int(retry_after)))}


_limiter = AuthRateLimiter()


def get_auth_rate_limiter() -> AuthRateLimiter:
    return _limiter


def reset_auth_rate_limiter() -> None:
    _limiter.reset()
