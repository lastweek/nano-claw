"""Helpers for coordinating automatic context compaction."""

from __future__ import annotations


def run_auto_compaction_if_needed(agent, turn_id: int, on_event) -> None:
    """Compact older turns before the next model call when policy requires it."""
    decision = agent.context_compaction.build_decision(agent)
    if not decision.should_compact:
        return

    plan = agent.context_compaction._build_plan(agent, force=False)
    if not plan.turns_to_compact:
        return

    agent.logger.log_context_compaction_event(
        turn_id=turn_id,
        stage="started",
        reason=decision.reason,
        covered_turn_count=len(plan.turns_to_compact),
        retained_turn_count=len(plan.retained_turns),
    )
    agent._emit_turn_event(
        on_event,
        "context_compaction_started",
        reason=decision.reason,
        covered_turn_count=len(plan.turns_to_compact),
        retained_turn_count=len(plan.retained_turns),
    )

    result = agent.context_compaction.compact_now(
        agent,
        decision.reason,
        turn_id=turn_id,
        force=False,
    )
    if result.status == "skipped":
        agent.logger.log_context_compaction_event(
            turn_id=turn_id,
            stage="skipped",
            reason=result.reason,
            reason_text=agent.context_compaction.describe_reason(result.reason, result.details),
            **result.details,
        )
        return

    agent.logger.log_context_compaction_event(
        turn_id=turn_id,
        stage="completed",
        reason=decision.reason,
        covered_turn_count=result.covered_turn_count,
        retained_turn_count=result.retained_turn_count,
        before_tokens=result.before_tokens,
        after_tokens=result.after_tokens,
        **result.details,
    )
    summary = agent.context.get_summary()
    if summary is not None:
        agent.logger.record_compaction_snapshot(
            timestamp=summary.updated_at,
            compaction_count=summary.compaction_count,
            reason=decision.reason,
            covered_turn_count=summary.covered_turn_count,
            covered_message_count=summary.covered_message_count,
            retained_turn_count=result.retained_turn_count,
            before_tokens=result.before_tokens,
            after_tokens=result.after_tokens,
            used_fallback=result.used_fallback,
            rendered_text=summary.rendered_text,
            payload=summary.payload,
        )
    agent._emit_turn_event(
        on_event,
        "context_compaction_completed",
        reason=decision.reason,
        covered_turn_count=result.covered_turn_count,
        retained_turn_count=result.retained_turn_count,
        before_tokens=result.before_tokens,
        after_tokens=result.after_tokens,
        used_fallback=result.used_fallback,
    )
