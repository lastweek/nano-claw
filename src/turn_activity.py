"""Turn activity events for live CLI progress display."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Literal, Optional


TurnActivityKind = Literal[
    "plan_mode_entered",
    "plan_written",
    "plan_submitted",
    "plan_approved",
    "plan_rejected",
    "plan_execution_started",
    "plan_cleared",
    "context_compaction_started",
    "context_compaction_completed",
    "context_compaction_failed",
    "skill_preload",
    "skill_normalized",
    "skill_load_requested",
    "skill_load_succeeded",
    "skill_load_failed",
    "llm_call_started",
    "llm_call_finished",
    "tool_call_started",
    "tool_call_finished",
    "subagent_started",
    "subagent_completed",
    "subagent_failed",
    "answer_stream_started",
    "turn_completed",
    "turn_error",
]


@dataclass(frozen=True)
class TurnActivityEvent:
    """A user-safe activity event emitted during a turn.

    `details` keys by event kind:
    - `plan_mode_entered`: `task`, `plan_id`, `file_path`
    - `plan_written`: `plan_id`, `file_path`
    - `plan_submitted`: `plan_id`, `file_path`, `summary`
    - `plan_approved`: `plan_id`, `file_path`
    - `plan_rejected`: `plan_id`, `file_path`
    - `plan_execution_started`: `plan_id`, `file_path`
    - `plan_cleared`: `plan_id`
    - `context_compaction_started`: `reason`, `covered_turn_count`,
      `retained_turn_count`
    - `context_compaction_completed`: `reason`, `covered_turn_count`,
      `retained_turn_count`, `before_tokens`, `after_tokens`,
      optional `used_fallback`
    - `context_compaction_failed`: `reason`, `error`
    - `skill_preload`: `skill_name`, `reason`, `source`, `catalog_visible`
    - `skill_normalized`: `content`, `reason`
    - `skill_load_requested`: `skill_name`
    - `skill_load_succeeded`: `skill_name`
    - `skill_load_failed`: `skill_name`, `error`
    - `llm_call_started`: `stream`, `message_count`, `tool_schema_count`
    - `llm_call_finished`: `stream`, `duration_s`, `prompt_tokens`,
      `completion_tokens`, `total_tokens`, `cached_tokens`, `has_tool_calls`,
      `tool_call_count`, `result_kind`, `assistant_preview`,
      `assistant_body`, `requested_tool_signatures`
    - `tool_call_started`: `tool_name`, `tool_call_id`, `arguments`
    - `tool_call_finished`: `tool_name`, `tool_call_id`, `arguments`, `success`,
      `duration_s`, optional `error`, `result_preview`, `result_body`
    - `subagent_started`: `subagent_id`, `label`, `task`
    - `subagent_completed`: `subagent_id`, `label`, `duration_s`, `summary`
    - `subagent_failed`: `subagent_id`, `label`, `duration_s`, `error`
    - `turn_completed`: `status`, `llm_call_count`, `tool_call_count`,
      `tools_used`, `skills_used`
    - `turn_error`: `phase`, `message`
    """

    kind: TurnActivityKind
    iteration: Optional[int] = None
    worker_id: str = "main"
    worker_label: str = "Main Agent"
    worker_kind: Literal["main", "subagent"] = "main"
    parent_worker_id: str | None = None
    timestamp: float = field(default_factory=perf_counter)
    details: dict[str, Any] = field(default_factory=dict)


TurnActivityCallback = Callable[[TurnActivityEvent], None]
