"""Redaction helpers for admin endpoints."""

from __future__ import annotations

import re
from typing import Any


DEFAULT_PREVIEW_LIMIT = 240
REDACTED_TOKEN = "***REDACTED***"

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[a-z0-9._\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{12,}\b"),
    re.compile(r"\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*['\"]?([^\s'\",]+)"),
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\b"),
]

_SECRET_KEY_RE = re.compile(r"(?i)(key|token|secret|password)")


def truncate_text(value: str, *, limit: int = DEFAULT_PREVIEW_LIMIT) -> str:
    """Truncate text to a preview window."""
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def redact_text(value: str) -> str:
    """Redact common secret-like fragments from text."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED_TOKEN, redacted)
    return redacted


def preview_text(
    value: str | None,
    *,
    redacted: bool = True,
    limit: int = DEFAULT_PREVIEW_LIMIT,
) -> str | None:
    """Render a nullable text preview with optional redaction."""
    if value is None:
        return None
    rendered = redact_text(value) if redacted else value
    return truncate_text(rendered, limit=limit)


def redact_config_object(value: Any) -> Any:
    """Recursively redact secret-looking keys in dict/list config structures."""
    if isinstance(value, dict):
        redacted_dict: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                redacted_dict[str(key)] = REDACTED_TOKEN
            else:
                redacted_dict[str(key)] = redact_config_object(item)
        return redacted_dict
    if isinstance(value, list):
        return [redact_config_object(item) for item in value]
    return value
