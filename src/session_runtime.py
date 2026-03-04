"""Session-level orchestration for plan/build mode transitions."""

from __future__ import annotations

from typing import Callable, Optional

from src.config import config
from src.plan_mode import (
    build_plan_execution_message,
    create_session_plan,
    mark_plan_approved,
    mark_plan_executing,
    mark_plan_rejected,
)
from src.tools import ToolProfile, build_tool_registry


class SessionRuntimeController:
    """Own session mode transitions and plan-workflow state changes."""

    def __init__(
        self,
        *,
        session_context,
        agent,
        skill_manager,
        mcp_manager=None,
        subagent_manager=None,
        apply_tool_profile: Optional[Callable[[ToolProfile], None]] = None,
        logger=None,
        runtime_config=None,
    ) -> None:
        self.session_context = session_context
        self.agent = agent
        self.skill_manager = skill_manager
        self.mcp_manager = mcp_manager
        self.subagent_manager = subagent_manager
        self.apply_tool_profile = apply_tool_profile
        self.logger = logger or getattr(agent, "logger", None)
        self.runtime_config = runtime_config or config

    def activate_build_mode(self) -> None:
        """Switch the session back to normal build mode."""
        self.session_context.set_session_mode("build")
        self._apply_tool_profile(ToolProfile.BUILD)

    def activate_plan_mode(
        self,
        *,
        create_plan_if_missing: bool = False,
        task: str | None = None,
    ):
        """Switch the session into plan mode and optionally create a draft plan."""
        plan = self.session_context.get_current_plan()
        if create_plan_if_missing and plan is None:
            plan = create_session_plan(
                self.session_context,
                task=task or "Interactive planning session",
                plan_dir=self.runtime_config.plan.plan_dir,
            )

        self.session_context.set_session_mode("plan")
        self._apply_tool_profile(ToolProfile.PLAN_MAIN)
        return plan

    def toggle_plan_mode(self) -> None:
        """Toggle the top-level session mode between build and plan."""
        if not self.runtime_config.plan.enabled:
            return

        if self.session_context.get_session_mode() == "plan":
            self.activate_build_mode()
            return

        self.activate_plan_mode(create_plan_if_missing=True)

    def start_planning(self, task: str):
        """Create or replace the current draft plan and enter plan mode."""
        plan = create_session_plan(
            self.session_context,
            task=task,
            plan_dir=self.runtime_config.plan.plan_dir,
        )
        self.session_context.set_session_mode("plan")
        self._apply_tool_profile(ToolProfile.PLAN_MAIN)
        self._log_plan_event(
            stage="started",
            plan_id=plan.plan_id,
            status=plan.status,
            file_path=plan.file_path,
            task=plan.task,
        )
        return plan

    def mark_current_plan_approved(self):
        """Approve the current plan and activate its execution contract."""
        approved_plan = mark_plan_approved(self.session_context)
        self._log_plan_event(
            stage="approved",
            plan_id=approved_plan.plan_id,
            status=approved_plan.status,
            file_path=approved_plan.file_path,
        )
        return approved_plan

    def mark_current_plan_rejected(self):
        """Reject the current plan and clear any active contract."""
        rejected_plan = mark_plan_rejected(self.session_context)
        self._log_plan_event(
            stage="rejected",
            plan_id=rejected_plan.plan_id,
            status=rejected_plan.status,
            file_path=rejected_plan.file_path,
        )
        return rejected_plan

    def prepare_current_plan_for_execution(self):
        """Ensure the current plan is approved, mark it executing, and switch back to build mode."""
        current_plan = self.session_context.get_current_plan()
        if current_plan is None:
            raise ValueError("No session plan exists yet")

        if self.session_context.active_approved_plan_id != current_plan.plan_id:
            current_plan = self.mark_current_plan_approved()

        executing_plan = mark_plan_executing(self.session_context)
        self.activate_build_mode()
        self._log_plan_event(
            stage="execution_started",
            plan_id=executing_plan.plan_id,
            status=executing_plan.status,
            file_path=executing_plan.file_path,
        )
        return executing_plan, build_plan_execution_message(executing_plan)

    def exit_plan_mode(self) -> None:
        """Leave plan mode without changing any existing plan artifact."""
        self.activate_build_mode()

    def clear_active_plan_contract(self) -> str | None:
        """Clear only the active approved-plan contract for the session."""
        cleared_plan_id = self.session_context.active_approved_plan_id
        if cleared_plan_id is None:
            return None

        current_plan = self.session_context.get_current_plan()
        self.session_context.clear_active_plan_contract()
        self._log_plan_event(
            stage="cleared",
            plan_id=cleared_plan_id,
            status=current_plan.status if current_plan is not None else None,
            file_path=current_plan.file_path if current_plan is not None else None,
        )
        return cleared_plan_id

    def _apply_tool_profile(self, tool_profile: ToolProfile) -> None:
        """Apply one tool profile to the active parent agent."""
        if self.apply_tool_profile is not None:
            self.apply_tool_profile(tool_profile)
            return

        rebuilt_tools = build_tool_registry(
            skill_manager=self.skill_manager,
            mcp_manager=self.mcp_manager,
            subagent_manager=self.subagent_manager,
            include_subagent_tool=True,
            tool_profile=tool_profile,
            runtime_config=self.runtime_config,
        )
        self.agent.set_tool_registry(rebuilt_tools)

    def _log_plan_event(self, *, stage: str, **details) -> None:
        """Write one plan lifecycle event when a logger is available."""
        if self.logger is None:
            return
        self.logger.log_plan_event(turn_id=None, stage=stage, **details)
