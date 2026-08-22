"""Redact query strings from HTTP access logs so secrets never hit disk."""

from __future__ import annotations

import logging
import re

# Uvicorn access lines look like: GET /api/inbox?token=secret HTTP/1.1
_QUERY_IN_REQUEST_LINE = re.compile(r"\?([^ \t]*)")

_installed = False


def strip_query_for_log(value: str) -> str:
    """Remove query strings from a URL or HTTP request line."""
    if not value or "?" not in value:
        return value
    return _QUERY_IN_REQUEST_LINE.sub("", value)


class AccessLogQueryFilter(logging.Filter):
    """Drop query strings from uvicorn access-log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = strip_query_for_log(record.msg)
        args = record.args
        if isinstance(args, dict):
            record.args = {
                key: strip_query_for_log(val) if isinstance(val, str) else val
                for key, val in args.items()
            }
        elif isinstance(args, tuple):
            record.args = tuple(
                strip_query_for_log(arg) if isinstance(arg, str) else arg for arg in args
            )
        return True


def install_access_log_redaction() -> None:
    """Attach the filter to uvicorn.access once per process."""
    global _installed
    if _installed:
        return
    logging.getLogger("uvicorn.access").addFilter(AccessLogQueryFilter())
    _installed = True
