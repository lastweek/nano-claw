"""Test Context class."""

import pytest
from src.context import Context
from pathlib import Path


class TestContext:
    """Test Context dataclass."""

    def test_create_context(self):
        """Test creating a context with default values."""
        context = Context(cwd=Path("/tmp"))
        assert context.cwd == Path("/tmp")
        assert isinstance(context.session_id, str)
        assert len(context.session_id) > 0
        assert context.messages == []

    def test_create_from_string(self):
        """Test Context.create classmethod."""
        context = Context.create(".")
        assert context.cwd.is_absolute()
        assert isinstance(context.cwd, Path)

    def test_add_message(self):
        """Test adding a message to history."""
        context = Context(cwd=Path("/tmp"))
        context.add_message("user", "Hello")
        assert len(context.messages) == 1
        assert context.messages[0] == {"role": "user", "content": "Hello"}

    def test_add_multiple_messages(self):
        """Test adding multiple messages."""
        context = Context(cwd=Path("/tmp"))
        context.add_message("user", "Hello")
        context.add_message("assistant", "Hi there")
        assert len(context.messages) == 2

    def test_get_messages(self):
        """Test getting messages returns the list."""
        context = Context(cwd=Path("/tmp"))
        context.add_message("user", "test")
        messages = context.get_messages()
        assert len(messages) == 1
        # Should return same list (not copy, based on our implementation)
        assert messages is context.messages

    def test_clear_messages(self):
        """Test clearing messages."""
        context = Context(cwd=Path("/tmp"))
        context.add_message("user", "test")
        context.clear_messages()
        assert len(context.messages) == 0

    def test_activate_skill(self):
        """Test pinning a skill."""
        context = Context(cwd=Path("/tmp"))
        context.activate_skill("pdf")
        assert context.get_active_skills() == ["pdf"]

    def test_activate_skill_does_not_duplicate(self):
        """Test pinning the same skill twice keeps one entry."""
        context = Context(cwd=Path("/tmp"))
        context.activate_skill("pdf")
        context.activate_skill("pdf")
        assert context.get_active_skills() == ["pdf"]

    def test_deactivate_skill(self):
        """Test unpinning a skill."""
        context = Context(cwd=Path("/tmp"))
        context.activate_skill("pdf")
        context.deactivate_skill("pdf")
        assert context.get_active_skills() == []

    def test_clear_skills(self):
        """Test clearing all pinned skills."""
        context = Context(cwd=Path("/tmp"))
        context.activate_skill("pdf")
        context.activate_skill("terraform")
        context.clear_skills()
        assert context.get_active_skills() == []

    def test_summary_storage_and_message_rendering(self):
        """Context should store and expose a synthetic summary message."""
        from src.context import CompactedContextSummary

        context = Context(cwd=Path("/tmp"))
        summary = CompactedContextSummary(
            updated_at="2026-03-02T00:00:00",
            compaction_count=1,
            covered_turn_count=3,
            covered_message_count=6,
            rendered_text="Conversation summary for earlier turns:\n- Refactor logger",
        )

        context.set_summary(summary)

        assert context.get_summary() == summary
        assert context.get_summary_message() == {
            "role": "assistant",
            "content": "Conversation summary for earlier turns:\n- Refactor logger",
        }

    def test_get_complete_turns_uses_longest_valid_prefix(self):
        """Malformed tails should be excluded from the compactable prefix."""
        context = Context(cwd=Path("/tmp"))
        context.messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "assistant", "content": "dangling"},
        ]

        turns = context.get_complete_turns()

        assert len(turns) == 2
        assert turns[0].user_message["content"] == "u1"
        assert turns[1].assistant_message["content"] == "a2"

    def test_replace_history_with_retained_turns_preserves_malformed_tail(self):
        """Compaction should preserve the non-compactable tail after retained turns."""
        context = Context(cwd=Path("/tmp"))
        context.messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "assistant", "content": "dangling"},
        ]

        retained_turns = context.get_complete_turns()[1:]
        context.replace_history_with_retained_turns(retained_turns)

        assert context.messages == [
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "assistant", "content": "dangling"},
        ]

    def test_auto_compaction_toggle(self):
        """Context should store a session-local auto-compaction toggle."""
        context = Context(cwd=Path("/tmp"))
        assert context.is_auto_compaction_enabled() is True

        context.set_auto_compaction(False)
        assert context.is_auto_compaction_enabled() is False
