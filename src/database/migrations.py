"""Schema migrations for HTTP persistence."""

from __future__ import annotations

import sqlite3


TARGET_SCHEMA_VERSION = 2


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _apply_v2(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "runs") and not _table_exists(connection, "turns"):
        connection.execute("ALTER TABLE runs RENAME TO turns")

    if (
        _table_exists(connection, "messages")
        and _column_exists(connection, "messages", "run_id")
        and not _column_exists(connection, "messages", "turn_id")
    ):
        connection.execute("ALTER TABLE messages RENAME COLUMN run_id TO turn_id")

    if _table_exists(connection, "sessions") and not _column_exists(connection, "sessions", "state"):
        connection.execute("ALTER TABLE sessions ADD COLUMN state TEXT NOT NULL DEFAULT 'active'")
    if _table_exists(connection, "sessions") and not _column_exists(connection, "sessions", "closed_at"):
        connection.execute("ALTER TABLE sessions ADD COLUMN closed_at TEXT")
    if _table_exists(connection, "sessions"):
        connection.execute("UPDATE sessions SET state = 'active' WHERE state IS NULL")


def apply_schema_migrations(connection: sqlite3.Connection) -> None:
    """Apply all schema migrations up to target version."""
    row = connection.execute("PRAGMA user_version").fetchone()
    current_version = int(row[0]) if row else 0

    if current_version < 2:
        _apply_v2(connection)
        connection.execute(f"PRAGMA user_version = {TARGET_SCHEMA_VERSION}")

