"""Context management for babyclaw."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Literal, Optional
import uuid

from src.message_types import ChatMessage
from src.utils import resolve_path


SessionMode = Literal["build", "plan"]
PlanStatus = Literal[
    "draft",
    "ready_for_review",
    "approved",
    "rejected",
    "executing",
    "completed",
]


@dataclass
class CompactedContextSummary:
    """Rolling summary that replaces older raw turns in-session."""

    updated_at: str
    compaction_count: int
    covered_turn_count: int
    covered_message_count: int
    rendered_text: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConversationTurn:
    """A complete persisted conversation turn."""

    index: int
    user_message: ChatMessage
    assistant_message: ChatMessage


@dataclass
class SessionPlan:
    """Session-local plan artifact and approval state."""

    plan_id: str
    status: PlanStatus
    task: str
    file_path: str
    content: str
    summary: str
    created_at: str
    updated_at: str
    approved_at: str | None = None
    report: str = ""


@dataclass
class Context:
    """Session context passed to all operations."""
    cwd: Path
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: List[ChatMessage] = field(default_factory=list)
    active_skills: List[str] = field(default_factory=list)
    summary: Optional[CompactedContextSummary] = None
    last_prompt_tokens: Optional[int] = None
    last_prompt_cached_tokens: Optional[int] = None
    last_context_window: Optional[int] = None
    auto_compaction_enabled: bool = True
    session_mode: SessionMode = "build"
    current_plan: SessionPlan | None = None
    active_approved_plan_id: str | None = None

    @classmethod
    def create(cls, cwd: str = ".") -> 'Context':
        """Create a new context with resolved working directory."""
        return cls(cwd=resolve_path(cwd))

    def add_message(self, role: str, content: Any) -> None:
        """Add a message to the conversation history."""
        self.messages.append({"role": role, "content": content})

    def get_messages(self) -> List[ChatMessage]:
        """Get all messages in the conversation history."""
        return self.messages

    def clear_messages(self) -> None:
        """Clear the conversation history."""
        self.messages.clear()
        self.summary = None
        self.last_prompt_tokens = None
        self.last_prompt_cached_tokens = None
        self.last_context_window = None

    def activate_skill(self, name: str) -> None:
        """Pin a skill for the current session."""
        if name not in self.active_skills:
            self.active_skills.append(name)

    def deactivate_skill(self, name: str) -> None:
        """Unpin a skill for the current session."""
        if name in self.active_skills:
            self.active_skills.remove(name)

    def clear_skills(self) -> None:
        """Clear all pinned skills."""
        self.active_skills.clear()

    def get_active_skills(self) -> List[str]:
        """Return pinned skill names."""
        return list(self.active_skills)

    def get_session_mode(self) -> SessionMode:
        """Return the current top-level session mode."""
        return self.session_mode

    def set_session_mode(self, mode: SessionMode) -> None:
        """Set the current top-level session mode."""
        self.session_mode = mode

    def get_current_plan(self) -> SessionPlan | None:
        """Return the current session-local plan artifact."""
        return self.current_plan

    def set_current_plan(self, plan: SessionPlan | None) -> None:
        """Replace the current session-local plan artifact."""
        self.current_plan = plan

    def clear_active_plan_contract(self) -> None:
        """Clear the currently active approved execution contract."""
        self.active_approved_plan_id = None

    def get_active_approved_plan(self) -> SessionPlan | None:
        """Return the currently active approved plan contract, if any."""
        if self.current_plan is None:
            return None
        if self.current_plan.plan_id != self.active_approved_plan_id:
            return None
        return self.current_plan

    def get_complete_turns(self) -> List[ConversationTurn]:
        """Return the longest valid alternating prefix of complete user/assistant turns."""
        turns: List[ConversationTurn] = []
        index = 0
        turn_index = 1

        while index + 1 < len(self.messages):
            user_message = self.messages[index]
            assistant_message = self.messages[index + 1]
            if user_message.get("role") != "user" or assistant_message.get("role") != "assistant":
                break

            turns.append(
                ConversationTurn(
                    index=turn_index,
                    user_message=user_message,
                    assistant_message=assistant_message,
                )
            )
            turn_index += 1
            index += 2

        return turns

    def get_summary(self) -> Optional[CompactedContextSummary]:
        """Return the current rolling summary, if any."""
        return self.summary

    def set_summary(self, summary: Optional[CompactedContextSummary]) -> None:
        """Replace the current rolling summary."""
        self.summary = summary

    def get_summary_message(self) -> Optional[ChatMessage]:
        """Return the synthetic assistant summary message for the next call."""
        if self.summary is None or not self.summary.rendered_text:
            return None
        return {"role": "assistant", "content": self.summary.rendered_text}

    def replace_history_with_retained_turns(self, retained_turns: List[ConversationTurn]) -> None:
        """Replace the compactable message prefix while preserving malformed tail messages."""
        complete_turns = self.get_complete_turns()
        prefix_message_count = len(complete_turns) * 2
        malformed_tail = self.messages[prefix_message_count:]

        new_messages: List[ChatMessage] = []
        for turn in retained_turns:
            new_messages.append(dict(turn.user_message))
            new_messages.append(dict(turn.assistant_message))
        new_messages.extend(dict(message) for message in malformed_tail)
        self.messages = new_messages

    def set_auto_compaction(self, enabled: bool) -> None:
        """Enable or disable auto-compaction for the current session."""
        self.auto_compaction_enabled = enabled

    def is_auto_compaction_enabled(self) -> bool:
        """Return whether auto-compaction is enabled for this session."""
        return self.auto_compaction_enabled
