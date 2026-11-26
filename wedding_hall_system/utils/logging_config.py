"""Structured logging with sensitive-data redaction (Phase 1, SRS §16.5).

Passwords, session tokens and full National ID values must never appear
in logs. The filter redacts known-sensitive keys in log messages and in
``extra`` dicts passed to the logger.
"""
from __future__ import annotations

import logging
import re

_SENSITIVE_KEYS = ("password", "passwd", "token", "secret", "national_id", "salt", "hash")
_REDACTED = "[REDACTED]"
_VALUE_RE = re.compile(
    r"((?:password|token|national_id|salt)\s*['\"=:]?\s*['\"]?)([^'\",\s}]+)",
    re.IGNORECASE,
)


def _redact_text(text: str) -> str:
    return _VALUE_RE.sub(r"\1" + _REDACTED, text)


class RedactingFilter(logging.Filter):
    """Drop sensitive values from log records before they are emitted."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (_REDACTED if any(s in k.lower() for s in _SENSITIVE_KEYS) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _redact_text(str(a)) if isinstance(a, str) else a for a in record.args
                )
        return True


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the ``wedding_hall`` logger once; return it."""
    logger = logging.getLogger("wedding_hall")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger(name: str = "wedding_hall") -> logging.Logger:
    return logging.getLogger(name)
