"""Persistent session and turn storage for the local HTTP database runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from threading import Lock
from typing import Any
import uuid

from src.context import CompactedContextSummary, Context
from src.database.connection import initialize_database, managed_database_connection
from src.utils import utc_now


def _new_id(prefix: str) -> str:
    """Return a short prefixed identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class SessionRecord:
    """Stored summary for one persisted HTTP session."""

    id: str
    title: str
    summary_text: str | None
    state: str
    closed_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MessageRecord:
    """One persisted user/assistant session message."""

    seq: int
    role: str
    content: str


@dataclass(frozen=True)
class TurnRecord:
    """One persisted HTTP turn."""

    id: str
    session_id: str
    status: str
    input_text: str
    final_output: str | None
    error_text: str | None
    created_at: str
    started_at: str | None
    ended_at: str | None
    updated_at: str


@dataclass(frozen=True)
class SessionDetail:
    """Expanded session view used by the HTTP API."""

    session: SessionRecord
    messages: list[MessageRecord]
    recent_turns: list[TurnRecord]
    summary_json: dict | None


@dataclass(frozen=True)
class SessionSnapshot:
    """Persisted session snapshot used to hydrate one in-memory session runtime."""

    session: SessionRecord
    messages: list[MessageRecord]
    summary_json: dict | None


class SessionDatabase:
    """SQLite-backed session database for HTTP sessions and turns."""

    def __init__(self, db_path: Path):
        self.db_path = db_path.resolve()
        self._write_lock = Lock()

    def initialize(self) -> None:
        """Initialize the database schema and migrations."""
        initialize_database(self.db_path)

    def mark_incomplete_turns_failed(self) -> int:
        """Mark orphaned queued/running turns as failed after a restart."""
        now = utc_now()
        with self._write_lock, managed_database_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE turns
                SET status = 'failed',
                    error_text = 'Server restarted before turn completion.',
                    ended_at = ?,
                    updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (now, now),
            )
            connection.commit()
            return cursor.rowcount

    def create_session(self, title: str | None) -> SessionRecord:
        """Create and persist a new active session."""
        now = utc_now()
        session = SessionRecord(
            id=_new_id("sess"),
            title=(title or "").strip() or datetime.now().strftime("Session %Y-%m-%d %H:%M"),
            summary_text=None,
            state="active",
            closed_at=None,
            created_at=now,
            updated_at=now,
        )
        with self._write_lock, managed_database_connection(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO sessions(id, title, summary_text, summary_json, state, closed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.title,
                    None,
                    None,
                    session.state,
                    session.closed_at,
                    session.created_at,
                    session.updated_at,
                ),
            )
            connection.commit()
        return session

    def close_session(self, session_id: str) -> SessionRecord:
        """Mark one session closed. Idempotent for already-closed sessions."""
        now = utc_now()
        with self._write_lock, managed_database_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET state = 'closed',
                    closed_at = COALESCE(closed_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, session_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown session: {session_id}")
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session: {session_id}")
        return session

    def delete_session(self, session_id: str) -> None:
        """Delete one persisted session and any associated transcript/turn rows."""
        with self._write_lock, managed_database_connection(self.db_path) as connection:
            connection.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            connection.commit()

    def list_sessions(self) -> list[SessionRecord]:
        """Return persisted sessions ordered newest first."""
        with managed_database_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, title, summary_text, state, closed_at, created_at, updated_at
                FROM sessions
                ORDER BY updated_at DESC, created_at DESC
                """
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def list_active_session_ids(self) -> list[str]:
        """Return active session identifiers."""
        with managed_database_connection(self.db_path) as connection:
            rows = connection.execute(
                "SELECT id FROM sessions WHERE state = 'active' ORDER BY created_at ASC"
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord | None:
        """Return one session row or None."""
        with managed_database_connection(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, title, summary_text, state, closed_at, created_at, updated_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_session(row) if row is not None else None

    def create_turn(self, session_id: str, input_text: str) -> TurnRecord:
        """Create a queued turn for the given active session."""
        now = utc_now()
        turn = TurnRecord(
            id=_new_id("turn"),
            session_id=session_id,
            status="queued",
            input_text=input_text,
            final_output=None,
            error_text=None,
            created_at=now,
            started_at=None,
            ended_at=None,
            updated_at=now,
        )
        with self._write_lock, managed_database_connection(self.db_path) as connection:
            self._require_active_session(connection, session_id)
            connection.execute(
                """
                INSERT INTO turns(
                    id, session_id, status, input_text, final_output, error_text,
                    created_at, started_at, ended_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.id,
                    turn.session_id,
                    turn.status,
                    turn.input_text,
                    turn.final_output,
                    turn.error_text,
                    turn.created_at,
                    turn.started_at,
                    turn.ended_at,
                    turn.updated_at,
                ),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            connection.commit()
        return turn

    def set_turn_running(self, turn_id: str) -> None:
        """Mark one queued turn as running."""
        now = utc_now()
        with self._write_lock, managed_database_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE turns
                SET status = 'running',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, turn_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown turn: {turn_id}")

    def finish_turn_success(self, turn_id: str, *, final_output: str) -> None:
        """Mark one turn completed."""
        now = utc_now()
        with self._write_lock, managed_database_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE turns
                SET status = 'completed',
                    final_output = ?,
                    error_text = NULL,
                    ended_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (final_output, now, now, turn_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown turn: {turn_id}")

    def finish_turn_failure(self, turn_id: str, *, error_text: str) -> None:
        """Mark one turn failed."""
        now = utc_now()
        with self._write_lock, managed_database_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE turns
                SET status = 'failed',
                    error_text = ?,
                    ended_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_text, now, now, turn_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown turn: {turn_id}")

    def get_turn(self, turn_id: str) -> TurnRecord | None:
        """Return one persisted turn or None."""
        with managed_database_connection(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT id, session_id, status, input_text, final_output, error_text,
                       created_at, started_at, ended_at, updated_at
                FROM turns
                WHERE id = ?
                """,
                (turn_id,),
            ).fetchone()
        return self._row_to_turn(row) if row is not None else None

    def list_turns(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> tuple[list[TurnRecord], str | None]:
        """List turns with optional filters and offset cursor pagination."""
        normalized_limit = max(1, min(limit, 500))
        offset = 0
        if cursor:
            try:
                offset = max(int(cursor), 0)
            except ValueError:
                offset = 0

        where_clauses: list[str] = []
        params: list[object] = []
        if session_id:
            where_clauses.append("session_id = ?")
            params.append(session_id)
        if status:
            where_clauses.append("status = ?")
            params.append(status)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        query = f"""
            SELECT id, session_id, status, input_text, final_output, error_text,
                   created_at, started_at, ended_at, updated_at
            FROM turns
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([normalized_limit + 1, offset])

        with managed_database_connection(self.db_path) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()

        has_more = len(rows) > normalized_limit
        page_rows = rows[:normalized_limit]
        next_cursor = str(offset + normalized_limit) if has_more else None
        return [self._row_to_turn(row) for row in page_rows], next_cursor

    def get_session_detail(self, session_id: str) -> SessionDetail | None:
        """Return one expanded session detail view or None."""
        with managed_database_connection(self.db_path) as connection:
            session_snapshot = self._read_session_snapshot(connection, session_id)
            if session_snapshot is None:
                return None

            turn_rows = connection.execute(
                """
                SELECT id, session_id, status, input_text, final_output, error_text,
                       created_at, started_at, ended_at, updated_at
                FROM turns
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (session_id,),
            ).fetchall()

        return SessionDetail(
            session=session_snapshot.session,
            messages=session_snapshot.messages,
            recent_turns=[self._row_to_turn(row) for row in turn_rows],
            summary_json=session_snapshot.summary_json,
        )

    def get_session_snapshot(self, session_id: str) -> SessionSnapshot | None:
        """Return one lightweight session snapshot for runtime hydration."""
        with managed_database_connection(self.db_path) as connection:
            return self._read_session_snapshot(connection, session_id)

    def replace_session_snapshot(self, session_id: str, turn_id: str, context: Context) -> None:
        """Replace the persisted session snapshot with the current context view."""
        summary_text, summary_json = self._serialize_summary(context.get_summary())
        messages = self._filter_transcript_messages(context.get_messages())
        now = utc_now()

        with self._write_lock, managed_database_connection(self.db_path) as connection:
            session_cursor = connection.execute(
                """
                UPDATE sessions
                SET summary_text = ?, summary_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary_text, summary_json, now, session_id),
            )
            if session_cursor.rowcount == 0:
                raise KeyError(f"Unknown session: {session_id}")

            connection.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            for index, message in enumerate(messages, start=1):
                self._insert_message(
                    connection,
                    session_id=session_id,
                    turn_id=turn_id,
                    seq=index,
                    role=message["role"],
                    content=message["content"],
                    created_at=now,
                )
            connection.commit()

    def append_session_snapshot_delta(
        self,
        session_id: str,
        turn_id: str,
        context: Context,
        *,
        persisted_message_count: int,
    ) -> None:
        """Append only new transcript messages and refresh session summary."""
        messages = self._filter_transcript_messages(context.get_messages())
        if persisted_message_count > len(messages):
            raise ValueError("Persisted message count exceeds current context message count.")

        summary_text, summary_json = self._serialize_summary(context.get_summary())
        new_messages = messages[persisted_message_count:]
        now = utc_now()

        with self._write_lock, managed_database_connection(self.db_path) as connection:
            session_cursor = connection.execute(
                """
                UPDATE sessions
                SET summary_text = ?, summary_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary_text, summary_json, now, session_id),
            )
            if session_cursor.rowcount == 0:
                raise KeyError(f"Unknown session: {session_id}")

            for index, message in enumerate(new_messages, start=persisted_message_count + 1):
                self._insert_message(
                    connection,
                    session_id=session_id,
                    turn_id=turn_id,
                    seq=index,
                    role=message["role"],
                    content=message["content"],
                    created_at=now,
                )
            connection.commit()

    def count_turns_by_status(self) -> dict[str, int]:
        """Return aggregate turn counts keyed by status."""
        with managed_database_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM turns
                GROUP BY status
                """
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def count_messages_by_session(self) -> dict[str, int]:
        """Return message counts keyed by session id."""
        with managed_database_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT session_id, COUNT(*) AS count
                FROM messages
                GROUP BY session_id
                """
            ).fetchall()
        return {str(row["session_id"]): int(row["count"]) for row in rows}

    def count_turns_by_session(self) -> dict[str, int]:
        """Return turn counts keyed by session id."""
        with managed_database_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT session_id, COUNT(*) AS count
                FROM turns
                GROUP BY session_id
                """
            ).fetchall()
        return {str(row["session_id"]): int(row["count"]) for row in rows}

    def _require_active_session(self, connection, session_id: str) -> None:
        session_row = connection.execute(
            "SELECT state FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session_row is None:
            raise KeyError(f"Unknown session: {session_id}")
        if str(session_row["state"]) != "active":
            raise ValueError("Session is closed.")

    def _insert_message(
        self,
        connection,
        *,
        session_id: str,
        turn_id: str,
        seq: int,
        role: str,
        content: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO messages(id, session_id, turn_id, seq, role, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("msg"),
                session_id,
                turn_id,
                seq,
                role,
                content,
                created_at,
            ),
        )

    def _row_to_session(self, row) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            title=row["title"],
            summary_text=row["summary_text"],
            state=str(row["state"]),
            closed_at=row["closed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_message(self, row) -> MessageRecord:
        return MessageRecord(
            seq=int(row["seq"]),
            role=row["role"],
            content=row["content"],
        )

    def _row_to_turn(self, row) -> TurnRecord:
        return TurnRecord(
            id=row["id"],
            session_id=row["session_id"],
            status=row["status"],
            input_text=row["input_text"],
            final_output=row["final_output"],
            error_text=row["error_text"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            updated_at=row["updated_at"],
        )

    def _read_session_snapshot(self, connection, session_id: str) -> SessionSnapshot | None:
        session_row = connection.execute(
            """
            SELECT id, title, summary_text, summary_json, state, closed_at, created_at, updated_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session_row is None:
            return None

        message_rows = connection.execute(
            """
            SELECT seq, role, content
            FROM messages
            WHERE session_id = ?
            ORDER BY seq ASC
            """,
            (session_id,),
        ).fetchall()
        summary_json = json.loads(session_row["summary_json"]) if session_row["summary_json"] else None
        return SessionSnapshot(
            session=self._row_to_session(session_row),
            messages=[self._row_to_message(row) for row in message_rows],
            summary_json=summary_json,
        )

    @staticmethod
    def _serialize_summary(summary: CompactedContextSummary | None) -> tuple[str | None, str | None]:
        if summary is None:
            return None, None
        return summary.rendered_text, json.dumps(asdict(summary))

    @staticmethod
    def _filter_transcript_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        persisted: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role", ""))
            if role not in {"user", "assistant"}:
                continue
            persisted.append(
                {
                    "role": role,
                    "content": str(message.get("content", "")),
                }
            )
        return persisted


def deserialize_session_summary(payload: dict | None) -> CompactedContextSummary | None:
    """Rebuild compacted summary dataclass from stored JSON."""
    if payload is None:
        return None
    return CompactedContextSummary(**payload)
