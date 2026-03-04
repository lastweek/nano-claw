"""Shared state types for session logging."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


def sanitize_fragment(value: str) -> str:
    """Convert a freeform label into a filesystem-safe fragment."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value.strip()).strip("-") or "subagent"


@dataclass
class TurnState:
    """In-memory tracking for a single turn."""

    turn_id: int
    started_at: str
    raw_user_input: str
    normalized_user_input: str
    llm_call_count: int = 0
    tool_call_count: int = 0
    tools_used: List[str] = field(default_factory=list)
    skills_used: List[str] = field(default_factory=list)
    status: str = "open"


@dataclass(frozen=True)
class SessionLogSnapshot:
    """In-memory aggregate view of one logger session."""

    session_dir: str
    llm_log: str
    events_log: str
    llm_call_count: int
    tool_call_count: int
    tools_used: List[str]
