"""Tests for the HTTP app SQLite database."""

import sqlite3

import pytest

from src.context import CompactedContextSummary, Context
from src.database.connection import managed_database_connection
from src.database.session_database import SessionDatabase


def test_initialize_and_session_lifecycle(temp_dir):
    """Database should initialize, create sessions, and list newest first."""
    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()

    older = database.create_session("Older")
    newer = database.create_session("Newer")

    sessions = database.list_sessions()
    assert [session.id for session in sessions] == [newer.id, older.id]

    closed_once = database.close_session(newer.id)
    closed_twice = database.close_session(newer.id)
    assert closed_once.state == "closed"
    assert closed_twice.state == "closed"


def test_replace_session_snapshot_persists_only_user_assistant_messages(temp_dir):
    """Session snapshots should persist only the user/assistant transcript."""
    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("Transcript")
    turn = database.create_turn(session.id, "hello")

    context = Context(cwd=temp_dir, session_id=session.id)
    context.messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "tool", "content": "skip me"},
        {"role": "assistant", "content": "done"},
    ]
    context.set_summary(
        CompactedContextSummary(
            updated_at="2026-03-04T00:00:00",
            compaction_count=1,
            covered_turn_count=1,
            covered_message_count=2,
            rendered_text="summary",
            payload={"origin": "test"},
        )
    )

    database.replace_session_snapshot(session.id, turn.id, context)

    session_detail = database.get_session_detail(session.id)
    assert session_detail is not None
    assert [message.role for message in session_detail.messages] == ["user", "assistant", "assistant"]
    assert session_detail.summary_json is not None
    assert session_detail.summary_json["rendered_text"] == "summary"


def test_mark_incomplete_turns_failed(temp_dir):
    """Queued and running turns should be failed on server restart."""
    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("Restart")
    queued = database.create_turn(session.id, "queued")
    running = database.create_turn(session.id, "running")
    database.set_turn_running(running.id)

    updated = database.mark_incomplete_turns_failed()

    assert updated == 2
    assert database.get_turn(queued.id).status == "failed"
    assert database.get_turn(running.id).status == "failed"


def test_managed_database_connection_closes_connection_deterministically(temp_dir):
    """Managed DB context should always close the sqlite connection on exit."""
    db_path = temp_dir / "state.db"
    db_connection = None

    with managed_database_connection(db_path) as connection:
        db_connection = connection
        connection.execute("CREATE TABLE IF NOT EXISTS smoke_test(id INTEGER PRIMARY KEY)")
        connection.execute("SELECT 1").fetchone()
        connection.commit()

    assert db_connection is not None
    with pytest.raises(sqlite3.ProgrammingError):
        db_connection.execute("SELECT 1").fetchone()


def test_append_session_snapshot_delta_appends_only_new_messages(temp_dir):
    """Delta append should add only new transcript entries with contiguous sequencing."""
    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("Append")
    first_turn = database.create_turn(session.id, "hello")

    first_context = Context(cwd=temp_dir, session_id=session.id)
    first_context.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Echo: hello"},
    ]
    database.replace_session_snapshot(session.id, first_turn.id, first_context)

    second_turn = database.create_turn(session.id, "next")
    second_context = Context(cwd=temp_dir, session_id=session.id)
    second_context.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Echo: hello"},
        {"role": "user", "content": "next"},
        {"role": "assistant", "content": "Echo: next"},
        {"role": "tool", "content": "skip"},
    ]
    second_context.set_summary(
        CompactedContextSummary(
            updated_at="2026-03-04T00:00:00",
            compaction_count=0,
            covered_turn_count=0,
            covered_message_count=0,
            rendered_text="active summary",
            payload={"origin": "append-test"},
        )
    )

    database.append_session_snapshot_delta(
        session.id,
        second_turn.id,
        second_context,
        persisted_message_count=2,
    )

    detail = database.get_session_detail(session.id)
    assert detail is not None
    assert [message.seq for message in detail.messages] == [1, 2, 3, 4]
    assert [message.content for message in detail.messages] == [
        "hello",
        "Echo: hello",
        "next",
        "Echo: next",
    ]
    assert detail.session.summary_text == "active summary"


def test_get_session_snapshot_returns_transcript_and_summary(temp_dir):
    """Snapshot API should return transcript+summary without recent-turn payload."""
    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("Snapshot")
    turn = database.create_turn(session.id, "hello")
    context = Context(cwd=temp_dir, session_id=session.id)
    context.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Echo: hello"},
    ]
    context.set_summary(
        CompactedContextSummary(
            updated_at="2026-03-04T00:00:00",
            compaction_count=0,
            covered_turn_count=1,
            covered_message_count=2,
            rendered_text="summary text",
            payload={"origin": "snapshot-test"},
        )
    )
    database.replace_session_snapshot(session.id, turn.id, context)

    snapshot = database.get_session_snapshot(session.id)

    assert snapshot is not None
    assert snapshot.session.id == session.id
    assert [message.content for message in snapshot.messages] == ["hello", "Echo: hello"]
    assert snapshot.summary_json is not None
    assert snapshot.summary_json["rendered_text"] == "summary text"


def test_append_session_snapshot_delta_rejects_invalid_persisted_count(temp_dir):
    """Delta append should fail when persisted message count exceeds context messages."""
    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("AppendError")
    turn = database.create_turn(session.id, "hello")
    context = Context(cwd=temp_dir, session_id=session.id)
    context.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Echo: hello"},
    ]

    with pytest.raises(ValueError):
        database.append_session_snapshot_delta(
            session.id,
            turn.id,
            context,
            persisted_message_count=3,
        )
