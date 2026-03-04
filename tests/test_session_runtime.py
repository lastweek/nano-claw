"""Tests for centralized session mode and plan workflow transitions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.context import Context
from src.session_runtime import SessionRuntimeController
from src.tools import ToolProfile


class FakeLogger:
    """Record plan lifecycle events emitted by the session runtime."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def log_plan_event(self, **kwargs) -> None:
        self.events.append(kwargs)


def create_session_runtime(tmp_path):
    """Build a controller with a deterministic in-memory profile callback."""
    profile_changes: list[ToolProfile] = []
    logger = FakeLogger()
    context = Context.create(cwd=str(tmp_path))
    agent = SimpleNamespace(logger=logger)
    controller = SessionRuntimeController(
        session_context=context,
        agent=agent,
        skill_manager=SimpleNamespace(),
        apply_tool_profile=profile_changes.append,
        logger=logger,
    )
    return controller, context, profile_changes, logger


def test_activate_plan_mode_creates_draft_plan_and_uses_plan_profile(tmp_path):
    """Entering plan mode with creation enabled should create one draft plan."""
    controller, context, profile_changes, _ = create_session_runtime(tmp_path)

    plan = controller.activate_plan_mode(create_plan_if_missing=True, task="Plan the refactor")

    assert context.get_session_mode() == "plan"
    assert plan is not None
    assert plan.task == "Plan the refactor"
    assert Path(plan.file_path).name == f"{context.session_id}.md"
    assert profile_changes == [ToolProfile.PLAN_MAIN]


def test_start_planning_and_prepare_current_plan_for_execution(tmp_path):
    """Starting planning then preparing execution should approve and activate the contract."""
    controller, context, profile_changes, logger = create_session_runtime(tmp_path)

    started_plan = controller.start_planning("Implement the controller cleanup")
    executing_plan, execution_message = controller.prepare_current_plan_for_execution()

    assert started_plan.task == "Implement the controller cleanup"
    assert context.get_session_mode() == "build"
    assert executing_plan.status == "executing"
    assert context.active_approved_plan_id == executing_plan.plan_id
    assert executing_plan.file_path in execution_message
    assert profile_changes == [ToolProfile.PLAN_MAIN, ToolProfile.BUILD]
    assert [event["stage"] for event in logger.events] == [
        "started",
        "approved",
        "execution_started",
    ]


def test_clear_active_plan_contract_keeps_plan_artifact(tmp_path):
    """Clearing the active contract should not delete the current plan state."""
    controller, context, _, logger = create_session_runtime(tmp_path)

    controller.start_planning("Finalize the planning workflow")
    executing_plan, _ = controller.prepare_current_plan_for_execution()
    cleared_plan_id = controller.clear_active_plan_contract()

    assert cleared_plan_id == executing_plan.plan_id
    assert context.active_approved_plan_id is None
    assert context.get_current_plan() is not None
    assert context.get_current_plan().plan_id == executing_plan.plan_id
    assert logger.events[-1]["stage"] == "cleared"
