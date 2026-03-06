"""SQLite helpers for the local HTTP runtime."""

from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Iterator

from src.store.migrations import apply_schema_migrations

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


def connect_db(db_path: Path) -> sqlite3.Connection:
    """Open one SQLite connection with repo-friendly defaults."""
    connection = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@contextmanager
def managed_db_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open one SQLite connection and always close it when done."""
    connection = connect_db(db_path)
    try:
        yield connection
    finally:
        connection.close()


def initialize_db(db_path: Path) -> None:
    """Create database directories and initialize the schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with managed_db_connection(db_path) as connection:
        apply_schema_migrations(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.commit()
