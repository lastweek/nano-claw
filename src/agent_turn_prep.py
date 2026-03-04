"""Helpers for preparing one top-level agent turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.message_types import ChatMessage


@dataclass(frozen=True)
class PendingSkillEvent:
    """A deferred skill event emitted once a concrete turn id exists."""

    name: str
    details: dict[str, Any]


@dataclass(frozen=True)
class PreparedTurnInput:
    """Normalized user input and any skill preloads needed for a turn."""

    normalized_user_message: str
    preload_skill_names: list[str]
    pending_skill_events: list[PendingSkillEvent]


def prepare_turn_input(user_message: str, *, context, skill_manager) -> PreparedTurnInput:
    """Normalize a user message and collect skill preloads for the turn."""
    pending_skill_events: list[PendingSkillEvent] = []
    normalized_user_message = user_message
    preload_skill_names: list[str] = []

    if skill_manager is None:
        return PreparedTurnInput(
            normalized_user_message=normalized_user_message,
            preload_skill_names=preload_skill_names,
            pending_skill_events=pending_skill_events,
        )

    pinned_skill_names = [
        skill_name
        for skill_name in context.get_active_skills()
        if skill_manager.get_skill(skill_name) is not None
    ]
    preload_skill_names.extend(pinned_skill_names)
    for skill_name in pinned_skill_names:
        skill = skill_manager.get_skill(skill_name)
        if skill is None:
            continue
        pending_skill_events.append(
            PendingSkillEvent(
                name="preload",
                details={
                    "skill_name": skill.name,
                    "reason": "pinned",
                    "source": skill.source,
                    "catalog_visible": skill.catalog_visible,
                    "skill_file": str(skill.skill_file),
                },
            )
        )

    mention_result = skill_manager.extract_skill_mentions(user_message)
    explicit_skill_names = [
        skill_name
        for skill_name in mention_result.skill_names
        if skill_name not in preload_skill_names
    ]
    preload_skill_names.extend(explicit_skill_names)
    for skill_name in explicit_skill_names:
        skill = skill_manager.get_skill(skill_name)
        if skill is None:
            continue
        pending_skill_events.append(
            PendingSkillEvent(
                name="preload",
                details={
                    "skill_name": skill.name,
                    "reason": "explicit",
                    "source": skill.source,
                    "catalog_visible": skill.catalog_visible,
                    "skill_file": str(skill.skill_file),
                },
            )
        )

    if mention_result.cleaned_text:
        normalized_user_message = mention_result.cleaned_text
    elif mention_result.skill_names:
        normalized_user_message = "Use the preloaded skill context for this request."
        pending_skill_events.append(
            PendingSkillEvent(
                name="normalized_user_message",
                details={
                    "reason": "explicit_skill_only",
                    "content": normalized_user_message,
                },
            )
        )

    return PreparedTurnInput(
        normalized_user_message=normalized_user_message,
        preload_skill_names=preload_skill_names,
        pending_skill_events=pending_skill_events,
    )


def build_conversation_messages(
    *,
    system_message: ChatMessage,
    summary_message: ChatMessage | None,
    history_messages: list[ChatMessage],
    skill_manager,
    preload_skill_names: list[str],
    normalized_user_message: str,
    role_user: str,
) -> list[ChatMessage]:
    """Build the message list for the next LLM call."""
    messages: list[ChatMessage] = [system_message]

    if summary_message is not None:
        messages.append(summary_message)

    messages.extend(history_messages)

    if skill_manager is not None and preload_skill_names:
        messages.extend(skill_manager.build_preload_messages(preload_skill_names))

    messages.append({"role": role_user, "content": normalized_user_message})
    return messages
