"""Test constants defined in tools module."""

from src.tools import ROLE_SYSTEM, ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL


def test_role_constants():
    """Test that role constants have correct values."""
    assert ROLE_SYSTEM == "system"
    assert ROLE_USER == "user"
    assert ROLE_ASSISTANT == "assistant"
    assert ROLE_TOOL == "tool"
