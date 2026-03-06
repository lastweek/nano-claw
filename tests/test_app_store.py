"""Tests for the HTTP app SQLite store."""

import sqlite3

import pytest

from src.context import CompactedContextSummary, Context
from src.store.db import managed_db_connection
from src.store.repository import AppStore


def test_init_db_and_session_lifecycle(temp_dir):
    """Store should initialize, create sessions, and list newest first."""
    store = AppStore(temp_dir / "state.db")
    store.init_db()

    older = store.create_session("Older")
    newer = store.create_session("Newer")

    sessions = store.list_sessions()
    assert [session.id for session in sessions] == [newer.id, older.id]

    closed_once = store.close_session(newer.id)
    closed_twice = store.close_session(newer.id)
    assert closed_once.state == "closed"
    assert closed_twice.state == "closed"


def test_replace_session_snapshot_persists_only_user_assistant_messages(temp_dir):
    """Session snapshots should store only the user/assistant transcript."""
    store = AppStore(temp_dir / "state.db")
    store.init_db()
    session = store.create_session("Transcript")
    turn = store.create_turn(session.id, "hello")

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

    store.replace_session_snapshot(session.id, turn.id, context)

    session_detail = store.get_session_detail(session.id)
    assert session_detail is not None
    assert [message.role for message in session_detail.messages] == ["user", "assistant", "assistant"]
    assert session_detail.summary_json is not None
    assert session_detail.summary_json["rendered_text"] == "summary"


def test_mark_incomplete_turns_failed(temp_dir):
    """Queued and running turns should be failed on server restart."""
    store = AppStore(temp_dir / "state.db")
    store.init_db()
    session = store.create_session("Restart")
    queued = store.create_turn(session.id, "queued")
    running = store.create_turn(session.id, "running")
    store.set_turn_running(running.id)

    updated = store.mark_incomplete_turns_failed()

    assert updated == 2
    assert store.get_turn(queued.id).status == "failed"
    assert store.get_turn(running.id).status == "failed"


def test_managed_db_connection_closes_connection_deterministically(temp_dir):
    """Managed DB context should always close the sqlite connection on exit."""
    db_path = temp_dir / "state.db"
    db_connection = None

    with managed_db_connection(db_path) as connection:
        db_connection = connection
        connection.execute("CREATE TABLE IF NOT EXISTS smoke_test(id INTEGER PRIMARY KEY)")
        connection.execute("SELECT 1").fetchone()
        connection.commit()

    assert db_connection is not None
    with pytest.raises(sqlite3.ProgrammingError):
        db_connection.execute("SELECT 1").fetchone()


def test_append_session_snapshot_delta_appends_only_new_messages(temp_dir):
    """Delta append should add only new transcript entries with contiguous sequencing."""
    store = AppStore(temp_dir / "state.db")
    store.init_db()
    session = store.create_session("Append")
    first_turn = store.create_turn(session.id, "hello")

    first_context = Context(cwd=temp_dir, session_id=session.id)
    first_context.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Echo: hello"},
    ]
    store.replace_session_snapshot(session.id, first_turn.id, first_context)

    second_turn = store.create_turn(session.id, "next")
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

    store.append_session_snapshot_delta(
        session.id,
        second_turn.id,
        second_context,
        persisted_message_count=2,
    )

    detail = store.get_session_detail(session.id)
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
    store = AppStore(temp_dir / "state.db")
    store.init_db()
    session = store.create_session("Snapshot")
    turn = store.create_turn(session.id, "hello")
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
    store.replace_session_snapshot(session.id, turn.id, context)

    snapshot = store.get_session_snapshot(session.id)

    assert snapshot is not None
    assert snapshot.session.id == session.id
    assert [message.content for message in snapshot.messages] == ["hello", "Echo: hello"]
    assert snapshot.summary_json is not None
    assert snapshot.summary_json["rendered_text"] == "summary text"


def test_append_session_snapshot_delta_rejects_invalid_persisted_count(temp_dir):
    """Delta append should fail when persisted message count exceeds context messages."""
    store = AppStore(temp_dir / "state.db")
    store.init_db()
    session = store.create_session("AppendError")
    turn = store.create_turn(session.id, "hello")
    context = Context(cwd=temp_dir, session_id=session.id)
    context.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Echo: hello"},
    ]

    with pytest.raises(ValueError):
        store.append_session_snapshot_delta(
            session.id,
            turn.id,
            context,
            persisted_message_count=3,
        )
