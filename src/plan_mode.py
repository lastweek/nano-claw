"""Helpers for session-local planning mode and approved-plan execution."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from src.context import Context, SessionPlan


def _utc_now() -> str:
    """Return the current timestamp in ISO-8601 format."""
    return datetime.now().isoformat()


def build_plan_file_path(session_context: Context, plan_dir: str) -> Path:
    """Return the canonical persisted plan path for one session."""
    base_dir = Path(plan_dir)
    if not base_dir.is_absolute():
        base_dir = session_context.cwd / base_dir
    return base_dir / f"{session_context.session_id}.md"


def create_session_plan(session_context: Context, *, task: str, plan_dir: str) -> SessionPlan:
    """Create or replace the current session plan metadata."""
    timestamp = _utc_now()
    plan_path = build_plan_file_path(session_context, plan_dir)
    existing_content = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    plan = SessionPlan(
        plan_id=f"plan-{session_context.session_id[:8]}",
        status="draft",
        task=task,
        file_path=str(plan_path),
        content=existing_content,
        summary="",
        created_at=timestamp,
        updated_at=timestamp,
    )
    session_context.set_current_plan(plan)
    session_context.clear_active_plan_contract()
    return plan


def update_session_plan(
    session_context: Context,
    *,
    content: str | None = None,
    summary: str | None = None,
    report: str | None = None,
    status: str | None = None,
) -> SessionPlan:
    """Update the current session plan and persist the new dataclass in context."""
    current_plan = session_context.get_current_plan()
    if current_plan is None:
        raise ValueError("No active session plan")

    updated_plan = replace(
        current_plan,
        content=current_plan.content if content is None else content,
        summary=current_plan.summary if summary is None else summary,
        report=current_plan.report if report is None else report,
        status=current_plan.status if status is None else status,
        updated_at=_utc_now(),
    )
    session_context.set_current_plan(updated_plan)
    return updated_plan


def write_plan_content(session_context: Context, content: str) -> SessionPlan:
    """Persist plan content to the canonical plan file and update session state."""
    current_plan = session_context.get_current_plan()
    if current_plan is None:
        raise ValueError("No active session plan")

    plan_path = Path(current_plan.file_path)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(content, encoding="utf-8")
    return update_session_plan(session_context, content=content, status="draft")


def mark_plan_ready_for_review(
    session_context: Context,
    *,
    summary: str,
    report: str,
) -> SessionPlan:
    """Mark the current plan ready for user review after submit_plan."""
    return update_session_plan(
        session_context,
        summary=summary,
        report=report,
        status="ready_for_review",
    )


def mark_plan_approved(session_context: Context) -> SessionPlan:
    """Mark the current plan approved and activate it as an execution contract."""
    current_plan = session_context.get_current_plan()
    if current_plan is None:
        raise ValueError("No active session plan")

    approved_plan = replace(
        current_plan,
        status="approved",
        approved_at=_utc_now(),
        updated_at=_utc_now(),
    )
    session_context.set_current_plan(approved_plan)
    session_context.active_approved_plan_id = approved_plan.plan_id
    return approved_plan


def mark_plan_rejected(session_context: Context) -> SessionPlan:
    """Mark the current plan rejected and clear any active execution contract."""
    rejected_plan = update_session_plan(session_context, status="rejected")
    session_context.clear_active_plan_contract()
    return rejected_plan


def mark_plan_executing(session_context: Context) -> SessionPlan:
    """Mark the current plan as actively executing."""
    current_plan = session_context.get_current_plan()
    if current_plan is None:
        raise ValueError("No active session plan")

    if session_context.active_approved_plan_id != current_plan.plan_id:
        session_context.active_approved_plan_id = current_plan.plan_id
    return update_session_plan(session_context, status="executing")


def build_plan_prompt(
    session_context: Context,
    *,
    can_write_plan: bool = True,
    can_submit_plan: bool = True,
) -> str:
    """Build the planning-mode instruction block for the system prompt."""
    current_plan = session_context.get_current_plan()
    plan_path = current_plan.file_path if current_plan is not None else "the canonical session plan file"
    lines = [
        "You are currently in planning mode.",
        "Your job is to inspect the repository, clarify scope, and produce an executable implementation plan.",
        "Do not make code changes or run unrestricted commands in planning mode.",
        "You may inspect files, search the repository, load skills, and launch read-only research subagents when available.",
        "The plan should cover:",
        "- goal",
        "- scope and non-goals",
        "- key files or areas",
        "- implementation steps",
        "- risks or open questions",
        "- verification",
    ]
    if can_write_plan:
        lines.append(f"Write the canonical plan artifact at: {plan_path}")
    else:
        lines.append("You are a read-only planning worker. Do not write the canonical plan artifact yourself.")
    if can_submit_plan:
        lines.append("End the planning turn by calling submit_plan once the plan is ready for user review.")
    else:
        lines.append("Return research findings only. The main planner owns plan submission.")
    if current_plan is not None and current_plan.content:
        lines.extend(
            [
                "",
                "Current draft plan content:",
                current_plan.content,
            ]
        )
    return "\n".join(lines)


def build_build_execution_contract(session_context: Context) -> str:
    """Build the approved-plan execution contract for build turns."""
    active_plan = session_context.get_active_approved_plan()
    if active_plan is None:
        return ""

    lines = [
        "An approved implementation plan is active for this session.",
        f"Approved plan path: {active_plan.file_path}",
        "Follow this plan by default.",
        "Do not silently diverge from the approved plan.",
        "If reality in the repository materially differs from the plan, explain the deviation and update the plan explicitly.",
        "Verification steps in the plan are part of the task, not optional cleanup.",
        "",
        "Approved plan:",
        active_plan.content or "(plan file is currently empty)",
    ]
    return "\n".join(lines)


def build_plan_execution_message(plan: SessionPlan) -> str:
    """Build the synthetic build-mode message used after plan approval."""
    return (
        f"Execute the approved plan at {plan.file_path}.\n\n"
        f"Follow the approved implementation steps and report any material deviation.\n\n"
        f"Plan summary:\n{plan.summary or '(no summary provided)'}"
    )
