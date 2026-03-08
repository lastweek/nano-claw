"""Session-local context compaction with rolling handoff summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

from src.context import CompactedContextSummary, Context
from src.context_usage import (
    build_context_usage_snapshot,
    estimate_json_tokens,
)

_HANDOFF_SCHEMA_VERSION = 1
_HANDOFF_FIELDS: tuple[tuple[str, str], ...] = (
    ("goal", "Goal"),
    ("active_work", "Active work"),
    ("important_decisions_in_effect", "Important decisions in effect"),
    ("key_discoveries", "Key discoveries"),
    ("completed_work", "Completed work"),
    ("working_set_files", "Working set files"),
    ("open_loops", "Open loops"),
    ("next_steps", "Next steps"),
    ("risks_or_blockers", "Risks or blockers"),
)
_HANDOFF_ITEM_LIMIT = 5
_HANDOFF_ITEM_CHAR_LIMIT = 220


@dataclass(frozen=True)
class ContextCompactionPolicy:
    """Runtime policy for automatic context compaction."""

    auto_compact: bool
    auto_compact_threshold: float
    target_usage_after_compaction: float
    min_recent_turns: int


@dataclass(frozen=True)
class ContextCompactionDecision:
    """Decision about whether auto-compaction should run."""

    should_compact: bool
    reason: str
    current_used_tokens: int
    context_window: int | None
    threshold_tokens: int | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextCompactionResult:
    """Outcome of one compaction attempt."""

    status: Literal["compacted", "skipped", "failed"]
    reason: str
    covered_turn_count: int
    retained_turn_count: int
    summary_tokens: int
    before_tokens: int
    after_tokens: int
    used_fallback: bool = False
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _CompactionPlan:
    """Internal selection of turns to summarize versus retain."""

    turns_to_compact: list[Any]
    retained_turns: list[Any]
    before_tokens: int
    context_window: int | None
    total_turn_count: int
    evictable_turn_count: int
    effective_retained_turn_count: int
    target_tokens: int | None
    reason: str


class ContextCompactionManager:
    """Build decisions, summaries, and status for session-local compaction."""

    def __init__(
        self,
        llm_client,
        session_context: Context,
        skill_manager,
        policy: ContextCompactionPolicy,
    ) -> None:
        self.llm = llm_client
        self.session_context = session_context
        self.skill_manager = skill_manager
        self.policy = policy

    def build_decision(self, agent) -> ContextCompactionDecision:
        """Return whether auto-compaction should run before the next turn."""
        snapshot = build_context_usage_snapshot(agent, self.session_context, self.skill_manager)
        plan = self._build_plan(agent, force=False)
        if self.session_context.last_prompt_tokens is not None:
            current_used_tokens = self.session_context.last_prompt_tokens
            context_window = (
                self.session_context.last_context_window
                if self.session_context.last_context_window is not None
                else snapshot.context_window
            )
            metrics_source = "last_prompt"
        else:
            current_used_tokens = snapshot.used_tokens
            context_window = snapshot.context_window
            metrics_source = "estimate"

        threshold_tokens = None if context_window is None else int(
            context_window * self.policy.auto_compact_threshold
        )
        details = self._build_debug_details(
            plan,
            current_used_tokens=current_used_tokens,
            threshold_tokens=threshold_tokens,
            force=False,
        )
        details["metrics_source"] = metrics_source

        if not self.policy.auto_compact:
            return ContextCompactionDecision(
                should_compact=False,
                reason="config_disabled",
                current_used_tokens=current_used_tokens,
                context_window=context_window,
                threshold_tokens=threshold_tokens,
                details=details,
            )

        if not self.session_context.is_auto_compaction_enabled():
            return ContextCompactionDecision(
                should_compact=False,
                reason="session_disabled",
                current_used_tokens=current_used_tokens,
                context_window=context_window,
                threshold_tokens=threshold_tokens,
                details=details,
            )

        if context_window is None:
            return ContextCompactionDecision(
                should_compact=False,
                reason="unknown_context_window",
                current_used_tokens=current_used_tokens,
                context_window=context_window,
                threshold_tokens=threshold_tokens,
                details=details,
            )

        if not plan.turns_to_compact:
            return ContextCompactionDecision(
                should_compact=False,
                reason=plan.reason,
                current_used_tokens=current_used_tokens,
                context_window=context_window,
                threshold_tokens=threshold_tokens,
                details=details,
            )

        if current_used_tokens < threshold_tokens:
            return ContextCompactionDecision(
                should_compact=False,
                reason="below_threshold",
                current_used_tokens=current_used_tokens,
                context_window=context_window,
                threshold_tokens=threshold_tokens,
                details=details,
            )

        return ContextCompactionDecision(
            should_compact=True,
            reason="threshold_reached",
            current_used_tokens=current_used_tokens,
            context_window=context_window,
            threshold_tokens=threshold_tokens,
            details=details,
        )

    def compact_now(
        self,
        agent,
        reason: str,
        *,
        turn_id: int | None = None,
        force: bool = False,
    ) -> ContextCompactionResult:
        """Compact older session turns into a rolling summary."""
        plan = self._build_plan(agent, force=force)
        details = self._build_debug_details(
            plan,
            current_used_tokens=plan.before_tokens,
            threshold_tokens=plan.target_tokens if force else (
                int(plan.context_window * self.policy.auto_compact_threshold)
                if plan.context_window is not None
                else None
            ),
            force=force,
        )
        if not plan.turns_to_compact:
            return ContextCompactionResult(
                status="skipped",
                reason=plan.reason,
                covered_turn_count=self._existing_covered_turns(),
                retained_turn_count=len(plan.retained_turns),
                summary_tokens=self._current_summary_tokens(),
                before_tokens=plan.before_tokens,
                after_tokens=plan.before_tokens,
                used_fallback=False,
                details=details,
            )

        try:
            self._prune_tool_outputs(plan)
            payload, rendered_text = self._generate_summary(plan.turns_to_compact, reason, turn_id)
            summary_error = None
        except Exception as exc:
            payload, rendered_text = self._build_fallback_summary(plan.turns_to_compact)
            summary_error = str(exc)

        previous_summary = self.session_context.get_summary()
        compaction_count = (previous_summary.compaction_count if previous_summary else 0) + 1
        covered_turn_count = (previous_summary.covered_turn_count if previous_summary else 0) + len(plan.turns_to_compact)
        covered_message_count = (previous_summary.covered_message_count if previous_summary else 0) + (len(plan.turns_to_compact) * 2)
        summary = CompactedContextSummary(
            updated_at=datetime.now().isoformat(),
            compaction_count=compaction_count,
            covered_turn_count=covered_turn_count,
            covered_message_count=covered_message_count,
            rendered_text=rendered_text,
            payload=payload,
        )
        self.session_context.set_summary(summary)
        self.session_context.replace_history_with_retained_turns(plan.retained_turns)

        after_snapshot = build_context_usage_snapshot(agent, self.session_context, self.skill_manager)
        result_details = {**details, "used_fallback": summary_error is not None}
        if summary_error is not None:
            result_details["fallback_error"] = summary_error
        return ContextCompactionResult(
            status="compacted",
            reason=reason,
            covered_turn_count=covered_turn_count,
            retained_turn_count=len(plan.retained_turns),
            summary_tokens=estimate_json_tokens(self.session_context.get_summary_message()),
            before_tokens=plan.before_tokens,
            after_tokens=after_snapshot.used_tokens,
            used_fallback=summary_error is not None,
            error=None,
            details=result_details,
        )

    def render_summary_for_cli(self) -> str:
        """Return the current rolling summary text for `/compact show`."""
        summary = self.session_context.get_summary()
        if summary is None:
            return "No compacted summary is available for this session."
        return summary.rendered_text

    def render_status_snapshot(self, agent) -> dict[str, Any]:
        """Return current compaction status for command rendering."""
        snapshot = build_context_usage_snapshot(agent, self.session_context, self.skill_manager)
        decision = self.build_decision(agent)
        context_window = decision.context_window or snapshot.context_window
        current_used_tokens = decision.current_used_tokens
        current_used_percentage = (
            (current_used_tokens / context_window) * 100
            if context_window
            else None
        )
        summary = self.session_context.get_summary()
        return {
            "auto_compaction_enabled": self.session_context.is_auto_compaction_enabled(),
            "configured_auto_compact": self.policy.auto_compact,
            "auto_compact_threshold": self.policy.auto_compact_threshold,
            "target_usage_after_compaction": self.policy.target_usage_after_compaction,
            "min_recent_turns": self.policy.min_recent_turns,
            "effective_retained_turns": decision.details.get(
                "effective_retained_turns",
                len(self.session_context.get_complete_turns()),
            ),
            "current_used_tokens": current_used_tokens,
            "current_used_percentage": current_used_percentage,
            "summary_present": summary is not None,
            "summary_compaction_count": summary.compaction_count if summary else 0,
            "summary_covered_turn_count": summary.covered_turn_count if summary else 0,
            "summary_covered_message_count": summary.covered_message_count if summary else 0,
            "raw_retained_turn_count": len(self.session_context.get_complete_turns()),
            "context_window": context_window,
            "decision_should_compact": decision.should_compact,
            "decision_reason": decision.reason,
            "decision_reason_text": self.describe_reason(decision.reason, decision.details),
            "decision_details": decision.details,
            "threshold_tokens": decision.threshold_tokens,
        }

    def describe_reason(self, reason: str, details: Optional[dict[str, Any]] = None) -> str:
        """Return a user-facing description for a compaction reason code."""
        details = details or {}
        configured_min_recent_turns = details.get(
            "configured_min_recent_turns",
            details.get("min_recent_turns", self.policy.min_recent_turns),
        )
        effective_retained_turns = details.get("effective_retained_turns")
        current_used_tokens = details.get("current_used_tokens")
        threshold_tokens = details.get("threshold_tokens")

        if reason == "config_disabled":
            return "Auto-compaction is disabled in config."
        if reason == "session_disabled":
            return "Auto-compaction is disabled for this session."
        if reason == "unknown_context_window":
            return "The context window is unknown, so automatic compaction cannot decide when to run."
        if reason == "insufficient_turns":
            return (
                "Compaction requires at least 2 complete turns so nano-claw can keep "
                "at least 1 raw turn in session history."
            )
        if reason == "no_evictable_turns":
            if effective_retained_turns is not None:
                return (
                    "There are no older turns available to compact after applying adaptive retention "
                    f"(keeping {effective_retained_turns} raw turn(s), capped by configured retention "
                    f"of {configured_min_recent_turns})."
                )
            return "There are no older turns available to compact after applying adaptive retention."
        if reason == "below_threshold":
            if current_used_tokens is not None and threshold_tokens is not None:
                return (
                    "Estimated usage is below the auto-compaction threshold "
                    f"({current_used_tokens} < {threshold_tokens} tokens)."
                )
            return "Estimated usage is below the auto-compaction threshold."
        if reason == "threshold_reached":
            if current_used_tokens is not None and threshold_tokens is not None:
                return (
                    "Estimated usage reached the auto-compaction threshold "
                    f"({current_used_tokens} >= {threshold_tokens} tokens)."
                )
            return "Estimated usage reached the auto-compaction threshold."
        if reason == "manual_command":
            return "Manual compaction was requested."
        return reason.replace("_", " ")

    def render_debug_lines(self, details: Optional[dict[str, Any]] = None) -> list[str]:
        """Render deterministic debug lines for command output."""
        details = details or {}
        lines: list[str] = []

        if "complete_turn_count" in details:
            lines.append(f"Complete turns: {details['complete_turn_count']}")
        if "evictable_turn_count" in details:
            lines.append(f"Evictable turns: {details['evictable_turn_count']}")
        if "configured_min_recent_turns" in details:
            lines.append(
                f"Configured recent turns retained: {details['configured_min_recent_turns']}"
            )
        if "effective_retained_turns" in details:
            lines.append(
                f"Effective recent turns retained: {details['effective_retained_turns']}"
            )
        if details.get("force") is not None:
            lines.append(f"Force mode: {'yes' if details['force'] else 'no'}")
        if details.get("current_used_tokens") is not None:
            lines.append(f"Current baseline tokens: {details['current_used_tokens']}")
        if details.get("threshold_tokens") is not None:
            lines.append(f"Threshold tokens: {details['threshold_tokens']}")
        if details.get("target_tokens") is not None:
            lines.append(f"Target after compaction: {details['target_tokens']}")
        if details.get("context_window") is not None:
            lines.append(f"Context window: {details['context_window']}")
        return lines

    def _build_plan(self, agent, *, force: bool) -> _CompactionPlan:
        """Select which oldest turns should be compacted."""
        snapshot = build_context_usage_snapshot(agent, self.session_context, self.skill_manager)
        turns = self.session_context.get_complete_turns()
        target_tokens = (
            int(snapshot.context_window * self.policy.target_usage_after_compaction)
            if snapshot.context_window is not None
            else None
        )
        if len(turns) < 2:
            return _CompactionPlan(
                turns_to_compact=[],
                retained_turns=turns,
                before_tokens=snapshot.used_tokens,
                context_window=snapshot.context_window,
                total_turn_count=len(turns),
                evictable_turn_count=0,
                effective_retained_turn_count=len(turns),
                target_tokens=target_tokens,
                reason="insufficient_turns",
            )

        effective_retained_turn_count = min(self.policy.min_recent_turns, len(turns) - 1)
        retained_turns = turns[-effective_retained_turn_count:]
        evictable_turns = turns[:-effective_retained_turn_count]
        if not evictable_turns:
            return _CompactionPlan(
                turns_to_compact=[],
                retained_turns=retained_turns,
                before_tokens=snapshot.used_tokens,
                context_window=snapshot.context_window,
                total_turn_count=len(turns),
                evictable_turn_count=0,
                effective_retained_turn_count=effective_retained_turn_count,
                target_tokens=target_tokens,
                reason="no_evictable_turns",
            )

        if force:
            turns_to_compact = evictable_turns
            return _CompactionPlan(
                turns_to_compact=turns_to_compact,
                retained_turns=retained_turns,
                before_tokens=snapshot.used_tokens,
                context_window=snapshot.context_window,
                total_turn_count=len(turns),
                evictable_turn_count=len(evictable_turns),
                effective_retained_turn_count=effective_retained_turn_count,
                target_tokens=target_tokens,
                reason="manual_forced_selection",
            )

        if snapshot.context_window is None:
            return _CompactionPlan(
                turns_to_compact=[],
                retained_turns=turns,
                before_tokens=snapshot.used_tokens,
                context_window=snapshot.context_window,
                total_turn_count=len(turns),
                evictable_turn_count=len(evictable_turns),
                effective_retained_turn_count=effective_retained_turn_count,
                target_tokens=target_tokens,
                reason="unknown_context_window",
            )

        removed_tokens = 0
        turns_to_compact = []
        for turn in evictable_turns:
            turns_to_compact.append(turn)
            removed_tokens += estimate_json_tokens([turn.user_message, turn.assistant_message])
            if snapshot.used_tokens - removed_tokens <= target_tokens:
                break

        retained_turns = turns[len(turns_to_compact):]
        return _CompactionPlan(
            turns_to_compact=turns_to_compact,
            retained_turns=retained_turns,
            before_tokens=snapshot.used_tokens,
            context_window=snapshot.context_window,
            total_turn_count=len(turns),
            evictable_turn_count=len(evictable_turns),
            effective_retained_turn_count=len(retained_turns),
            target_tokens=target_tokens,
            reason="selected_turns",
        )

    def _build_debug_details(
        self,
        plan: _CompactionPlan,
        *,
        current_used_tokens: int,
        threshold_tokens: int | None,
        force: bool,
    ) -> dict[str, Any]:
        """Build deterministic diagnostics for compaction decisions and results."""
        return {
            "complete_turn_count": plan.total_turn_count,
            "evictable_turn_count": plan.evictable_turn_count,
            "configured_min_recent_turns": self.policy.min_recent_turns,
            "effective_retained_turns": plan.effective_retained_turn_count,
            "force": force,
            "current_used_tokens": current_used_tokens,
            "threshold_tokens": threshold_tokens,
            "target_tokens": plan.target_tokens,
            "context_window": plan.context_window,
            "plan_reason": plan.reason,
        }

    def _generate_summary(
        self,
        turns_to_compact: list[Any],
        reason: str,
        turn_id: int | None,
    ) -> tuple[dict[str, Any] | None, str]:
        """Ask the main model to merge older turns into one structured rolling handoff."""
        previous_summary = self.session_context.get_summary()
        previous_payload = self._normalize_handoff_payload(
            previous_summary.payload if previous_summary else None
        )
        prompt = {
            "previous_handoff": previous_payload,
            "turns_to_compact": [
                {
                    "turn_index": turn.index,
                    "user": turn.user_message.get("content", ""),
                    "assistant": turn.assistant_message.get("content", ""),
                }
                for turn in turns_to_compact
            ],
            "active_skills": self.session_context.get_active_skills(),
            "reason": reason,
        }
        schema = {
            "schema_version": _HANDOFF_SCHEMA_VERSION,
            "goal": ["short bullet"],
            "active_work": ["short bullet"],
            "important_decisions_in_effect": ["short bullet"],
            "key_discoveries": ["short bullet"],
            "completed_work": ["short bullet"],
            "working_set_files": ["path or file"],
            "open_loops": ["short bullet"],
            "next_steps": ["short bullet"],
            "risks_or_blockers": ["short bullet"],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are building a rolling session handoff for context compaction. "
                    "Return only one valid JSON object matching the provided schema. "
                    "Each field must be an array of short strings. Keep only high-value handoff state: "
                    "current goal, active work, important decisions, discoveries, completed work, "
                    "working files, open loops, next steps, and blockers. "
                    "This handoff is temporary session continuity, not durable profile memory. "
                    "Preserve continuity from the prior handoff when still relevant. "
                    "Do not include markdown fences, prose outside JSON, or raw transcript dumps."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Build the next rolling handoff JSON from the prior handoff plus these compacted turns.\n\n"
                    "SCHEMA EXAMPLE:\n"
                    f"{json.dumps(schema, ensure_ascii=True, indent=2)}\n\n"
                    "INPUT DATA (JSON):\n"
                    f"{json.dumps(prompt, ensure_ascii=True, indent=2)}"
                ),
            },
        ]

        response, _metrics = self.llm.chat(
            messages,
            tools=None,
            log_context=(
                {
                    "turn_id": turn_id,
                    "iteration": 0,
                    "stream": False,
                    "request_kind": "context_compaction",
                }
                if turn_id is not None
                else None
            ),
        )
        content = (response.get("content") or "").strip()
        if not content:
            raise ValueError("empty summarizer output")
        payload = self._parse_handoff_payload(content)
        return payload, self._render_handoff_payload(payload)

    def _build_fallback_summary(self, turns_to_compact: list[Any]) -> tuple[dict[str, Any] | None, str]:
        """Build a deterministic emergency handoff when structured summarization fails."""
        payload = self._build_fallback_handoff_payload(turns_to_compact)
        return payload, self._render_handoff_payload(payload)

    def _normalize_handoff_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Normalize arbitrary payloads into the stable handoff schema."""
        normalized: dict[str, Any] = {"schema_version": _HANDOFF_SCHEMA_VERSION}
        payload = payload or {}
        for key, _label in _HANDOFF_FIELDS:
            value = payload.get(key, [])
            items: list[str] = []
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, str):
                        continue
                    compact = " ".join(item.split()).strip()
                    if not compact:
                        continue
                    compact = self._truncate_text(compact, limit=_HANDOFF_ITEM_CHAR_LIMIT)
                    if compact not in items:
                        items.append(compact)
                    if len(items) >= _HANDOFF_ITEM_LIMIT:
                        break
            normalized[key] = items
        return normalized

    def _parse_handoff_payload(self, content: str) -> dict[str, Any]:
        """Parse one structured handoff JSON object from model output."""
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError("invalid compaction handoff JSON") from None
            payload = json.loads(text[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("compaction handoff must be a JSON object")
        return self._normalize_handoff_payload(payload)

    def _render_handoff_payload(self, payload: dict[str, Any]) -> str:
        """Render the structured handoff into deterministic prompt text."""
        lines = ["Session handoff for earlier turns:", ""]
        for key, label in _HANDOFF_FIELDS:
            lines.append(f"## {label}")
            items = payload.get(key, [])
            if items:
                for item in items:
                    lines.append(f"- {item}")
            else:
                lines.append("- none")
            lines.append("")
        lines.append("This handoff replaces older raw turns. Prefer recent raw turns if they conflict.")
        return "\n".join(lines).strip()

    def _build_fallback_handoff_payload(self, turns_to_compact: list[Any]) -> dict[str, Any]:
        """Build a deterministic structured handoff from the compacted turns."""
        previous_summary = self.session_context.get_summary()
        payload = self._normalize_handoff_payload(previous_summary.payload if previous_summary else None)
        goals: list[str] = []
        completed: list[str] = []
        next_steps: list[str] = []
        blockers: list[str] = []
        files: list[str] = list(payload.get("working_set_files", []))

        for turn in turns_to_compact:
            user_text = self._truncate_text(turn.user_message.get("content", ""))
            assistant_text = self._truncate_text(turn.assistant_message.get("content", ""))
            self._append_unique(goals, user_text)
            self._append_unique(completed, assistant_text)
            for match in self._extract_file_references(user_text):
                self._append_unique(files, match)
            for match in self._extract_file_references(assistant_text):
                self._append_unique(files, match)
            lowered_user = str(turn.user_message.get("content", "")).lower()
            lowered_assistant = str(turn.assistant_message.get("content", "")).lower()
            if any(token in lowered_user for token in ("remember to", "next", "todo", "follow up", "need to")):
                self._append_unique(next_steps, user_text)
            if any(token in lowered_user or token in lowered_assistant for token in ("blocked", "blocker", "failed", "cannot", "can't", "error")):
                self._append_unique(blockers, user_text)

        self._merge_handoff_items(payload, "goal", goals[:2])
        self._merge_handoff_items(payload, "active_work", goals[-2:])
        self._merge_handoff_items(payload, "completed_work", completed[-3:])
        self._merge_handoff_items(
            payload,
            "key_discoveries",
            ["Fallback handoff generated because structured compaction summarization failed."],
        )
        self._merge_handoff_items(payload, "working_set_files", files)
        self._merge_handoff_items(payload, "open_loops", next_steps[:2])
        self._merge_handoff_items(payload, "next_steps", next_steps[:3])
        self._merge_handoff_items(payload, "risks_or_blockers", blockers[:2])
        return payload

    def _merge_handoff_items(self, payload: dict[str, Any], key: str, values: list[str]) -> None:
        """Merge new items into one handoff section with stable dedupe."""
        current = list(payload.get(key, []))
        for value in values:
            self._append_unique(current, value)
            if len(current) >= _HANDOFF_ITEM_LIMIT:
                break
        payload[key] = current[:_HANDOFF_ITEM_LIMIT]

    def _append_unique(self, items: list[str], value: str) -> None:
        """Append one normalized item if it is non-empty and not already present."""
        compact = " ".join(str(value or "").split()).strip()
        if not compact:
            return
        if compact not in items:
            items.append(compact)

    def _extract_file_references(self, text: str) -> list[str]:
        """Return path-like references from one message body."""
        found: list[str] = []
        for token in str(text).replace("(", " ").replace(")", " ").split():
            candidate = token.strip(".,:;[]{}<>`'\"")
            if "/" not in candidate and "." not in candidate:
                continue
            if candidate.startswith(("http://", "https://")):
                continue
            if any(
                candidate.endswith(ext)
                for ext in (".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".js", ".ts", ".tsx", ".jsx")
            ) or "/" in candidate:
                self._append_unique(found, candidate)
            if len(found) >= _HANDOFF_ITEM_LIMIT:
                break
        return found

    def _truncate_text(self, value: Any, *, limit: int = 160) -> str:
        """Convert arbitrary values into stable, compact summary strings."""
        if isinstance(value, str):
            text = " ".join(value.split())
        else:
            text = " ".join(json.dumps(value, ensure_ascii=True, sort_keys=True).split())
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _prune_tool_outputs(self, plan: _CompactionPlan) -> int:
        """Replace old tool outputs with a placeholder if tool messages exist."""
        complete_turns = self.session_context.get_complete_turns()
        if not complete_turns:
            return 0

        retained_turn_count = len(plan.retained_turns)
        evictable_turn_count = max(len(complete_turns) - retained_turn_count, 0)
        cutoff = evictable_turn_count * 2
        if cutoff <= 0:
            return 0

        pruned = 0
        for message in self.session_context.messages[:cutoff]:
            if message.get("role") == "tool" and message.get("content"):
                message["content"] = "[tool output omitted]"
                pruned += 1
        return pruned

    def _existing_covered_turns(self) -> int:
        """Return the count of turns already represented by the rolling summary."""
        summary = self.session_context.get_summary()
        return summary.covered_turn_count if summary is not None else 0

    def _current_summary_tokens(self) -> int:
        """Estimate the current summary message token contribution."""
        return estimate_json_tokens(self.session_context.get_summary_message())
