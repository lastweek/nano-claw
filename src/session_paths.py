"""Shared helpers for canonical per-session filesystem paths."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import unicodedata

from src.utils import resolve_path

DEFAULT_SESSIONS_ROOT = "~/.babyclaw/sessions"
_SESSION_SLUG_MAX_LENGTH = 48


def resolve_sessions_root(path: str | Path = DEFAULT_SESSIONS_ROOT, base: Path | None = None) -> Path:
    """Resolve the canonical sessions root."""
    return resolve_path(path, base)


def slugify_session_title(title: str | None) -> str:
    """Return a filesystem-safe slug for one session title."""
    normalized = unicodedata.normalize("NFKD", (title or "").strip())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if not slug:
        return "session"
    trimmed = slug[:_SESSION_SLUG_MAX_LENGTH].strip("-")
    return trimmed or "session"


def session_folder_date(created_at: str | None) -> str:
    """Return the folder date prefix for one session."""
    if created_at:
        try:
            return datetime.fromisoformat(created_at.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
        if len(created_at) >= 10:
            candidate = created_at[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
                return candidate
    return datetime.now().date().isoformat()


def build_session_dir_name(
    session_id: str,
    *,
    title: str | None,
    created_at: str | None,
) -> str:
    """Build the stable readable directory name for one session."""
    return f"{session_folder_date(created_at)}-{slugify_session_title(title)}-{session_id}"


def find_existing_session_dir(sessions_root: Path, session_id: str) -> Path | None:
    """Find an existing session directory by its stable id suffix."""
    if not sessions_root.exists():
        return None
    suffix = f"-{session_id}"
    for child in sessions_root.iterdir():
        if child.is_dir() and child.name.endswith(suffix):
            return child
    return None


def resolve_session_dir(
    sessions_root: Path,
    session_id: str,
    *,
    title: str | None,
    created_at: str | None,
) -> Path:
    """Resolve the canonical per-session directory, preferring an existing path."""
    existing = find_existing_session_dir(sessions_root, session_id)
    if existing is not None:
        return existing
    return sessions_root / build_session_dir_name(
        session_id,
        title=title,
        created_at=created_at,
    )
