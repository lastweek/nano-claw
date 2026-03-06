"""Admin log resource helpers and safe log file access."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from src.server.admin_redaction import redact_text
from src.server.admin_schemas import build_list_resource, build_resource
from src.utils import resolve_path

TAIL_READ_BLOCK_BYTES = 8192


def collect_log_files(app: FastAPI, session_id: str, path: str | None) -> dict[str, Any]:
    """Collect LogFile resources for one session runtime log directory."""
    session_dir = _resolve_session_log_dir(app, session_id)
    if session_dir is None:
        return build_list_resource(
            kind="LogFile",
            items=[],
            metadata_extra={"sessionId": session_id, "phase": "NotLoaded"},
        )

    root = _resolve_log_root(app)
    target = _resolve_log_path(base=session_dir, relative_path=path or ".", root=root)
    entries: list[Path]
    if target.is_dir():
        entries = sorted(target.iterdir(), key=lambda entry: entry.name)
    else:
        entries = [target]

    items: list[dict[str, Any]] = []
    for entry in entries:
        entry_type = _entry_type(entry)
        stat = entry.stat()
        relative_entry = entry.relative_to(session_dir).as_posix()
        items.append(
            build_resource(
                kind="LogFile",
                name=relative_entry or ".",
                spec={
                    "session_id": session_id,
                    "relative_path": relative_entry or ".",
                    "absolute_path": str(entry),
                    "type": entry_type,
                },
                status={
                    "phase": "Ready",
                    "bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                },
            )
        )

    return build_list_resource(
        kind="LogFile",
        items=items,
        metadata_extra={"sessionId": session_id, "path": str(path or ".")},
    )


def collect_log_file_tail(
    app: FastAPI,
    *,
    session_id: str,
    file_path: str,
    lines: int,
    redacted: bool,
) -> dict[str, Any]:
    """Collect one tailed LogFile resource."""
    session_dir = _resolve_session_log_dir(app, session_id)
    if session_dir is None:
        return build_resource(
            kind="LogFile",
            name=file_path,
            spec={"session_id": session_id, "relative_path": file_path},
            status={"phase": "NotLoaded", "tail": []},
        )

    normalized_lines = max(1, min(lines, 2000))
    root = _resolve_log_root(app)
    target = _resolve_log_path(base=session_dir, relative_path=file_path, root=root)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(file_path)

    tailed_lines = _read_tailed_lines(target, normalized_lines)
    if redacted:
        tailed_lines = [redact_text(line) for line in tailed_lines]

    relative_entry = target.relative_to(session_dir).as_posix()
    return build_resource(
        kind="LogFile",
        name=relative_entry,
        spec={
            "session_id": session_id,
            "relative_path": relative_entry,
            "tail": tailed_lines,
            "line_limit": normalized_lines,
            "redacted": redacted,
        },
        status={"phase": "Ready", "line_count": len(tailed_lines)},
    )


def resolve_log_file_path(app: FastAPI, *, session_id: str, file_path: str) -> Path:
    """Resolve a safe absolute path for one session log file."""
    session_dir = _resolve_session_log_dir(app, session_id)
    if session_dir is None:
        raise FileNotFoundError(session_id)
    root = _resolve_log_root(app)
    target = _resolve_log_path(base=session_dir, relative_path=file_path, root=root)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(file_path)
    return target


def _resolve_log_root(app: FastAPI) -> Path:
    return resolve_path(app.state.runtime_config.logging.log_dir, app.state.repo_root)


def _resolve_session_log_dir(app: FastAPI, session_id: str) -> Path | None:
    snapshot = app.state.session_registry.snapshot_runtime(session_id)
    if snapshot is None:
        return None
    session_dir = snapshot.get("logger", {}).get("session_dir")
    if not session_dir:
        return None
    session_dir_path = Path(session_dir).resolve()
    root = _resolve_log_root(app)
    if not _is_within_path(session_dir_path, root):
        return None
    return session_dir_path


def _resolve_log_path(*, base: Path, relative_path: str, root: Path) -> Path:
    candidate = (base / relative_path).resolve()
    # The file must stay inside both the session directory and the configured log root
    # so runtime snapshots cannot be used to escape into arbitrary filesystem paths.
    if not _is_within_path(candidate, base):
        raise ValueError("Path traversal outside session log directory is not allowed.")
    if not _is_within_path(candidate, root):
        raise ValueError("Path must remain under configured log root.")
    if candidate.is_symlink():
        raise ValueError("Symlink paths are not allowed.")
    return candidate


def _is_within_path(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _entry_type(entry: Path) -> str:
    if entry.is_symlink():
        return "symlink"
    if entry.is_dir():
        return "directory"
    if entry.is_file():
        return "file"
    return "other"


def _read_tailed_lines(path: Path, line_limit: int) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        chunks: list[bytes] = []
        newline_count = 0

        while position > 0 and newline_count <= line_limit:
            read_size = min(TAIL_READ_BLOCK_BYTES, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")

    content = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return content.splitlines()[-line_limit:]
