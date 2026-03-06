"""Shared utility functions for nano-claw."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any


def env_truthy(var_name: str, default: bool = False) -> bool:
    """Check if environment variable evaluates to truthy.

    Returns True if the environment variable is set to a truthy value
    (1, true, yes, on). Case-insensitive.

    Args:
        var_name: Name of the environment variable to check
        default: Default value if variable is not set

    Returns:
        True if variable is set to a truthy value, False otherwise

    Examples:
        >>> os.environ["FEATURE_FLAG"] = "true"
        >>> env_truthy("FEATURE_FLAG")
        True
        >>> env_truthy("MISSING_VAR", default=True)
        True
    """
    raw_value = os.environ.get(var_name)
    if raw_value is None:
        return default
    return raw_value.lower() in ("1", "true", "yes", "on")


def resolve_path(path: str | Path = ".", base: Path | None = None) -> Path:
    """Resolve a path consistently with optional base directory.

    Args:
        path: Path to resolve (relative or absolute)
        base: Base directory for relative paths (defaults to cwd)

    Returns:
        Resolved absolute Path object

    Examples:
        >>> resolve_path("config.yaml")
        PosixPath('/current/working/dir/config.yaml')
        >>> resolve_path("data", base=Path("/tmp"))
        PosixPath('/tmp/data')
    """
    path_obj = Path(path)
    if base and not path_obj.is_absolute():
        return (base / path_obj).resolve()
    return path_obj.resolve()


def calculate_percentage(value: int, total: int | None) -> float | None:
    """Compute percentage with None-safe handling.

    Args:
        value: The numerator value
        total: The total/denominator value (None returns None)

    Returns:
        Percentage as float, or None if total is None or 0

    Examples:
        >>> calculate_percentage(50, 100)
        50.0
        >>> calculate_percentage(75, 200)
        37.5
        >>> calculate_percentage(10, None)
        None
    """
    if total in (None, 0):
        return None
    return (value / total) * 100


def utc_now() -> str:
    """Return current UTC time as ISO8601 string.

    Returns:
        ISO8601 formatted timestamp string

    Examples:
        >>> utc_now()
        '2025-03-05T12:34:56.789012'
    """
    return datetime.now().isoformat()
