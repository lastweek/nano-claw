"""Helpers for estimating next-call context usage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from src.config import Config
from src.utils import calculate_percentage


@dataclass(frozen=True)
class ContextUsageRow:
    """High-level category usage row."""

    category: str
    tokens: int
    percentage: float | None


@dataclass(frozen=True)
class ToolSchemaUsageRow:
    """Per-tool schema usage row."""

    name: str
    kind: str
    tokens: int


@dataclass(frozen=True)
class SkillUsageRow:
    """Per-skill usage row."""

    name: str
    source: str
    usage_type: Literal["catalog", "pinned"]
    tokens: int


@dataclass(frozen=True)
class MessageUsageRow:
    """Per-message usage row."""

    index: int
    role: str
    tokens: int
    preview: str


@dataclass(frozen=True)
class ContextUsageSnapshot:
    """Estimated context usage for the next call baseline."""

    model: str
    context_window: int | None
    used_tokens: int
    used_percentage: float | None
    free_tokens: int | None
    overflow_tokens: int
    categories: list[ContextUsageRow]
    tools: list[ToolSchemaUsageRow]
    skills: list[SkillUsageRow]
    messages: list[MessageUsageRow]
    notes: list[str]


def estimate_text_tokens(text: str) -> int:
    """Estimate token count with the repo's existing rough heuristic."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_json_tokens(payload: Any) -> int:
    """Estimate token count for JSON-like payloads."""
    if payload in (None, "", [], {}, ()):
        return 0
    return estimate_text_tokens(_json_dumps(payload))


def format_token_count(value: int | None) -> str:
    """Render token counts in compact human form."""
    if value is None:
        return "unknown"
    if value < 1000:
        return str(value)
    if value < 10000:
        return f"{value / 1000:.1f}k"
    return f"{round(value / 100) / 10:.1f}k"


def build_context_usage_snapshot(agent, session_context, skill_manager=None) -> ContextUsageSnapshot:
    """Build a next-call baseline context usage snapshot."""
    runtime_config = getattr(agent, "runtime_config", None) or Config.load()
    context_window = runtime_config.llm.context_window
    system_prompt = getattr(agent, "_cached_system_prompt_base", "") or ""
    skill_catalog = ""
    if hasattr(agent, "_build_skill_catalog_section"):
        skill_catalog = agent._build_skill_catalog_section() or ""

    if getattr(agent, "_cached_tool_schemas", None) is not None:
        tool_schemas = agent._cached_tool_schemas
    else:
        tool_schemas = agent.tools.get_tool_schemas()

    active_skill_names = []
    pinned_preload_messages: list[dict] = []
    skill_rows: list[SkillUsageRow] = []
    if skill_manager is not None:
        for skill in skill_manager.list_catalog_skills():
            catalog_line = f"- {skill.name}: {skill.short_description}"
            skill_rows.append(
                SkillUsageRow(
                    name=skill.name,
                    source=skill.source,
                    usage_type="catalog",
                    tokens=estimate_text_tokens(catalog_line),
                )
            )

        active_skill_names = [
            skill_name
            for skill_name in session_context.get_active_skills()
            if skill_manager.get_skill(skill_name) is not None
        ]
        for skill_name in active_skill_names:
            skill = skill_manager.get_skill(skill_name)
            if skill is None:
                continue
            preload_messages = skill_manager.build_preload_messages([skill.name])
            pinned_preload_messages.extend(preload_messages)
            skill_rows.append(
                SkillUsageRow(
                    name=skill.name,
                    source=skill.source,
                    usage_type="pinned",
                    tokens=estimate_json_tokens(preload_messages),
                )
            )

    persisted_messages = session_context.get_messages()
    compacted_summary_message = session_context.get_summary_message()
    message_rows = [
        MessageUsageRow(
            index=index,
            role=str(message.get("role", "")),
            tokens=estimate_json_tokens(message),
            preview=_build_message_preview(message.get("content")),
        )
        for index, message in enumerate(persisted_messages, start=1)
    ]

    tool_rows = []
    for tool, schema in zip(agent.tools._tools.values(), tool_schemas):
        tool_name = getattr(tool, "name", "")
        kind = "builtin"
        if hasattr(tool, "_server") and getattr(tool._server, "name", None):
            kind = f"mcp:{tool._server.name}"
        tool_rows.append(
            ToolSchemaUsageRow(
                name=tool_name,
                kind=kind,
                tokens=estimate_json_tokens(schema),
            )
        )

    category_token_map = {
        "System prompt": estimate_text_tokens(system_prompt),
        "Tool schemas": estimate_json_tokens(tool_schemas),
        "Skill catalog": estimate_text_tokens(skill_catalog),
        "Pinned skills": estimate_json_tokens(pinned_preload_messages),
        "Compacted summary": estimate_json_tokens(compacted_summary_message),
        "Messages": estimate_json_tokens(persisted_messages),
    }

    used_tokens = sum(category_token_map.values())
    used_percentage = _percentage(used_tokens, context_window)
    free_tokens = None
    overflow_tokens = 0
    if context_window is not None:
        if used_tokens <= context_window:
            free_tokens = context_window - used_tokens
        else:
            overflow_tokens = used_tokens - context_window

    categories = [
        ContextUsageRow(category=category, tokens=tokens, percentage=_percentage(tokens, context_window))
        for category, tokens in category_token_map.items()
    ]
    if context_window is not None:
        if overflow_tokens:
            categories.append(
                ContextUsageRow(
                    category="Over limit",
                    tokens=overflow_tokens,
                    percentage=_percentage(overflow_tokens, context_window),
                )
            )
        else:
            categories.append(
                ContextUsageRow(
                    category="Free space",
                    tokens=free_tokens or 0,
                    percentage=_percentage(free_tokens or 0, context_window),
                )
            )

    notes = ["Baseline excludes the next user message and any explicit $skill preloads."]
    notes.append(
        "Auto-compaction triggers at "
        f"{runtime_config.context.auto_compact_threshold * 100:.0f}% and targets "
        f"{runtime_config.context.target_usage_after_compaction * 100:.0f}% usage after compaction."
    )
    if compacted_summary_message is not None:
        notes.append("Recent messages exclude any turns already compacted into the rolling summary.")
    if context_window is None:
        notes.append("Context window is not configured; percentages and free-space estimates are unavailable.")
    elif overflow_tokens:
        notes.append(
            f"Estimated baseline exceeds configured context window by {format_token_count(overflow_tokens)}."
        )

    return ContextUsageSnapshot(
        model=getattr(agent.llm, "model", "unknown"),
        context_window=context_window,
        used_tokens=used_tokens,
        used_percentage=used_percentage,
        free_tokens=free_tokens,
        overflow_tokens=overflow_tokens,
        categories=categories,
        tools=tool_rows,
        skills=skill_rows,
        messages=message_rows,
        notes=notes,
    )


def _json_dumps(payload: Any) -> str:
    """Serialize payloads consistently for token estimation."""
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _percentage(value: int, total: int | None) -> float | None:
    """Compute percentage against the configured context window."""
    return calculate_percentage(value, total)


def _build_message_preview(content: Any) -> str:
    """Create a stable one-line preview for a persisted message."""
    if isinstance(content, str):
        preview = " ".join(content.split())
    else:
        preview = " ".join(_json_dumps(content).split())

    if len(preview) <= 60:
        return preview
    return preview[:57] + "..."
