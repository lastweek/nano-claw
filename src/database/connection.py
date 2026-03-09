"""SQLite connection helpers for the local HTTP session database."""

from __future__ import annotations

from contextlib import contextmanager
import shutil
import sqlite3
from pathlib import Path
from typing import Iterator

from src.database.migrations import apply_schema_migrations

DEFAULT_HTTP_DATABASE_PATH = "~/.babyclaw/state.db"
LEGACY_HTTP_DATABASE_PATH = ".babyclaw/state.db"
DB_ARTIFACT_SUFFIXES = ("", "-wal", "-shm")

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        summary_text TEXT,
        summary_json TEXT,
        state TEXT NOT NULL DEFAULT 'active',
        closed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS turns (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        status TEXT NOT NULL,
        input_text TEXT NOT NULL,
        final_output TEXT,
        error_text TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        ended_at TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_turns_session_created_at ON turns(session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_turns_status_created ON turns(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_session_turn ON messages(session_id, turn_id)",
)


def _db_artifact_paths(db_path: Path) -> tuple[Path, ...]:
    """Return the database file and SQLite sidecar paths."""
    return tuple(Path(f"{db_path}{suffix}") for suffix in DB_ARTIFACT_SUFFIXES)


def resolve_http_database_path(db_path: str | Path, repo_root: Path) -> Path:
    """Resolve the configured HTTP database path with home-directory expansion."""
    path_obj = Path(db_path).expanduser()
    if not path_obj.is_absolute():
        path_obj = repo_root / path_obj
    return path_obj.resolve()


def migrate_legacy_http_database(db_path: Path, repo_root: Path) -> str | None:
    """Move the legacy repo-local database into the default global location when safe."""
    default_global_db_path = resolve_http_database_path(DEFAULT_HTTP_DATABASE_PATH, repo_root)
    if db_path != default_global_db_path:
        return None

    legacy_db_path = (repo_root / LEGACY_HTTP_DATABASE_PATH).resolve()
    legacy_artifacts = _db_artifact_paths(legacy_db_path)
    target_artifacts = _db_artifact_paths(db_path)
    if not any(path.exists() for path in legacy_artifacts):
        return None

    if any(path.exists() for path in target_artifacts):
        return (
            "Legacy repo-local DB was left untouched at "
            f"{legacy_db_path} because global DB artifacts already exist at {db_path}."
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    for source_path, target_path in zip(legacy_artifacts, target_artifacts):
        if source_path.exists():
            shutil.move(str(source_path), str(target_path))

    return f"Migrated legacy repo-local DB from {legacy_db_path} to {db_path}."


def connect_database(db_path: Path) -> sqlite3.Connection:
    """Open one SQLite connection with repo-friendly defaults."""
    connection = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@contextmanager
def managed_database_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open one SQLite connection and always close it when done."""
    connection = connect_database(db_path)
    try:
        yield connection
    finally:
        connection.close()


def initialize_database(db_path: Path) -> None:
    """Create database directories and initialize the schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with managed_database_connection(db_path) as connection:
        apply_schema_migrations(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.commit()
