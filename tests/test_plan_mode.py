"""Tests for the session-local planning workflow."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from src.agent import Agent
from src.commands import builtin
from src.commands.registry import CommandRegistry
from src.config import Config
from src.context import Context
from src.plan_mode import (
    build_build_execution_contract,
    build_plan_execution_message,
    build_plan_prompt,
    create_session_plan,
    mark_plan_approved,
    write_plan_content,
)
from src.session_runtime import SessionRuntimeController
from src.skills import SkillManager
from src.subagents import SubagentManager
from src.tools import ToolProfile, build_tool_registry
from src.tools.plan_submit import SubmitPlanTool
from src.tools.plan_write import WritePlanTool
from src.tools.readonly_shell import ReadOnlyShellTool


class DummyParentLLM:
    """Minimal LLM stub for prompt-building agent tests."""

    def __init__(self) -> None:
        self.provider = "ollama"
        self.model = "llama3"
        self.base_url = "http://localhost:11434/v1"
        self.logger = None


class DummyChildLLM(DummyParentLLM):
    """Replacement child LLM used only to avoid real client initialization."""

    def __init__(self, provider=None, model=None, base_url=None, logger=None):
        super().__init__()
        self.provider = provider or self.provider
        self.model = model or self.model
        self.base_url = base_url or self.base_url
        self.logger = logger


def create_console(buffer: io.StringIO, *, terminal: bool = False) -> Console:
    """Create a deterministic Rich console for command assertions."""
    return Console(file=buffer, force_terminal=terminal, color_system=None, width=120)


def create_plan_command_context(temp_dir, *, prompt_answers=None):
    """Create a command registry and context for /plan command tests."""
    repo_root = temp_dir / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    skill_manager = SkillManager(repo_root=repo_root)
    skill_manager.discover()
    session_context = Context.create(cwd=str(repo_root))
    subagent_manager = SubagentManager(enabled=True)
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
        tool_profile=ToolProfile.BUILD,
    )
    agent = Agent(
        DummyParentLLM(),
        tools,
        session_context,
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
    )
    agent.logger.enabled = False

    profile_changes: list[ToolProfile] = []
    turn_prompts: list[str] = []
    answers = list(prompt_answers or [])

    command_context = {
        "agent": agent,
        "session_context": session_context,
        "skill_manager": skill_manager,
        "subagent_manager": subagent_manager,
    }

    def apply_tool_profile(tool_profile: ToolProfile) -> None:
        profile_changes.append(tool_profile)
        rebuilt_tools = build_tool_registry(
            skill_manager=skill_manager,
            subagent_manager=subagent_manager,
            include_subagent_tool=True,
            tool_profile=tool_profile,
        )
        agent.set_tool_registry(rebuilt_tools)
        command_context["tools"] = rebuilt_tools

    session_runtime = SessionRuntimeController(
        session_context=session_context,
        agent=agent,
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        apply_tool_profile=apply_tool_profile,
        logger=agent.logger,
    )

    def run_agent_turn_callback(prompt: str) -> str:
        turn_prompts.append(prompt)
        if session_context.get_session_mode() == "plan":
            current_plan = session_context.get_current_plan()
            assert current_plan is not None
            write_plan_content(session_context, "# Plan\n\n- inspect\n- implement\n- verify\n")
            current_plan = session_context.get_current_plan()
            assert current_plan is not None
            current_plan.summary = "Add the planning workflow"
            current_plan.report = "Planning report.\n\n1. Inspect the repo.\n2. Add the workflow.\n3. Verify it."
            current_plan.status = "ready_for_review"
            session_context.set_current_plan(current_plan)
            return current_plan.report
        return "Executed approved plan."

    def prompt_input_callback(prompt: str) -> str:
        assert answers, f"Unexpected prompt: {prompt}"
        return answers.pop(0)

    registry = CommandRegistry()
    builtin.register_all(registry)
    return (
        registry,
        {
            **command_context,
            "session_runtime_controller": session_runtime,
            "run_agent_turn_callback": run_agent_turn_callback,
            "prompt_input_callback": prompt_input_callback if prompt_answers is not None else None,
        },
        profile_changes,
        turn_prompts,
    )


def test_build_tool_registry_uses_plan_safe_profiles(monkeypatch, temp_dir):
    """Plan profiles should expose only planning-safe tools."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    Config.reload()

    repo_root = temp_dir / "repo"
    repo_root.mkdir()
    skill_manager = SkillManager(repo_root=repo_root)
    skill_manager.discover()
    subagent_manager = SubagentManager(enabled=True)

    build_tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
        tool_profile=ToolProfile.BUILD,
    )
    plan_main_tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
        tool_profile=ToolProfile.PLAN_MAIN,
    )
    plan_subagent_tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=False,
        tool_profile=ToolProfile.PLAN_SUBAGENT,
    )

    assert {"read_file", "write_file", "run_command", "load_skill", "run_subagent"} <= set(build_tools.list_tools())
    assert set(plan_main_tools.list_tools()) == {
        "read_file",
        "load_skill",
        "run_readonly_command",
        "write_plan",
        "submit_plan",
        "run_subagent",
    }
    assert set(plan_subagent_tools.list_tools()) == {
        "read_file",
        "load_skill",
        "run_readonly_command",
    }


def test_plan_prompt_and_execution_contract_include_expected_content(temp_dir):
    """Planning prompts and approved-plan contracts should expose the right workflow text."""
    session_context = Context.create(cwd=str(temp_dir))
    session_context.set_session_mode("plan")
    plan = create_session_plan(session_context, task="Add plan mode", plan_dir=".nano-claw/plans")
    write_plan_content(session_context, "# Plan\n\n- inspect\n- implement\n- verify\n")
    approved_plan = mark_plan_approved(session_context)

    plan_prompt = build_plan_prompt(session_context)
    execution_contract = build_build_execution_contract(session_context)
    execution_message = build_plan_execution_message(approved_plan)

    assert "planning mode" in plan_prompt
    assert approved_plan.file_path in plan_prompt
    assert "approved implementation plan is active" in execution_contract
    assert approved_plan.file_path in execution_contract
    assert approved_plan.content in execution_contract
    assert approved_plan.file_path in execution_message


def test_write_plan_tool_writes_only_canonical_plan_file(temp_dir):
    """write_plan should persist only the canonical plan file for the current session."""
    session_context = Context.create(cwd=str(temp_dir))
    plan = create_session_plan(session_context, task="Plan safely", plan_dir=".nano-claw/plans")
    unrelated_file = temp_dir / "other.md"

    result = WritePlanTool().execute(
        session_context,
        content="# Canonical Plan\n\n- one\n- two\n",
        file_path=str(unrelated_file),
    )

    assert result.success is True
    assert Path(plan.file_path).read_text(encoding="utf-8").startswith("# Canonical Plan")
    assert not unrelated_file.exists()


def test_submit_plan_tool_marks_plan_ready_for_review(temp_dir):
    """submit_plan should store review metadata on the session plan."""
    session_context = Context.create(cwd=str(temp_dir))
    create_session_plan(session_context, task="Plan safely", plan_dir=".nano-claw/plans")
    write_plan_content(session_context, "# Plan\n\n- inspect\n")

    result = SubmitPlanTool().execute(
        session_context,
        summary="Inspect then implement",
        report="Planning report.\n\nProceed carefully.",
    )

    current_plan = session_context.get_current_plan()
    assert result.success is True
    assert current_plan is not None
    assert current_plan.status == "ready_for_review"
    assert current_plan.summary == "Inspect then implement"
    assert current_plan.report.startswith("Planning report.")


def test_plan_start_accepts_and_executes_immediately(monkeypatch, temp_dir):
    """Accepting a submitted plan should switch back to build mode and execute it."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    Config.reload()
    registry, command_context, profile_changes, turn_prompts = create_plan_command_context(
        temp_dir,
        prompt_answers=["accept"],
    )
    output = io.StringIO()
    console = create_console(output, terminal=True)

    executed = registry.execute("/plan start add a planning workflow", console, command_context)

    session_context = command_context["session_context"]
    current_plan = session_context.get_current_plan()
    assert executed is True
    assert session_context.get_session_mode() == "build"
    assert current_plan is not None
    assert session_context.active_approved_plan_id == current_plan.plan_id
    assert current_plan.status == "executing"
    assert profile_changes == [ToolProfile.PLAN_MAIN, ToolProfile.BUILD]
    assert len(turn_prompts) == 2
    assert "Execute the approved plan" in turn_prompts[1]


def test_plan_start_rejects_and_returns_to_build_mode(monkeypatch, temp_dir):
    """Rejecting a submitted plan should return the session to build mode without execution."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    Config.reload()
    registry, command_context, profile_changes, turn_prompts = create_plan_command_context(
        temp_dir,
        prompt_answers=["reject"],
    )
    output = io.StringIO()
    console = create_console(output, terminal=True)

    executed = registry.execute("/plan start reject this plan", console, command_context)

    session_context = command_context["session_context"]
    current_plan = session_context.get_current_plan()
    assert executed is True
    assert session_context.get_session_mode() == "build"
    assert current_plan is not None
    assert current_plan.status == "rejected"
    assert session_context.active_approved_plan_id is None
    assert profile_changes == [ToolProfile.PLAN_MAIN, ToolProfile.BUILD]
    assert len(turn_prompts) == 1


def test_plan_apply_executes_ready_plan(monkeypatch, temp_dir):
    """`/plan apply` should mark a ready plan approved and execute it."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    Config.reload()
    registry, command_context, profile_changes, turn_prompts = create_plan_command_context(temp_dir)
    session_context = command_context["session_context"]
    plan = create_session_plan(session_context, task="Apply later", plan_dir=".nano-claw/plans")
    write_plan_content(session_context, "# Plan\n\n- later\n")
    plan = session_context.get_current_plan()
    assert plan is not None
    plan.summary = "Apply later"
    plan.report = "Later review report."
    plan.status = "ready_for_review"
    session_context.set_current_plan(plan)
    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("/plan apply", console, command_context)

    current_plan = session_context.get_current_plan()
    assert executed is True
    assert current_plan is not None
    assert current_plan.status == "executing"
    assert session_context.active_approved_plan_id == current_plan.plan_id
    assert profile_changes == [ToolProfile.BUILD]
    assert len(turn_prompts) == 1
    assert "Execute the approved plan" in turn_prompts[0]


def test_plan_mode_subagent_children_inherit_read_only_tools(monkeypatch, temp_dir):
    """Plan-mode subagents should not inherit write, unrestricted shell, or plan-finalization tools."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    Config.reload()
    monkeypatch.setattr("src.subagents.LLMClient", DummyChildLLM)

    repo_root = temp_dir / "repo"
    repo_root.mkdir()
    session_context = Context.create(cwd=str(repo_root))
    session_context.set_session_mode("plan")
    create_session_plan(session_context, task="Plan safely", plan_dir=".nano-claw/plans")
    skill_manager = SkillManager(repo_root=repo_root)
    skill_manager.discover()
    subagent_manager = SubagentManager(enabled=True)
    parent_tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
        tool_profile=ToolProfile.PLAN_MAIN,
    )
    parent_agent = Agent(
        DummyParentLLM(),
        parent_tools,
        session_context,
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
    )
    child_context = Context.create(cwd=str(repo_root))
    child_context.set_session_mode("plan")
    child_context.set_current_plan(session_context.get_current_plan())
    child_logger = SimpleNamespace(enabled=False, start_session=lambda **_: None)
    child_agent = subagent_manager._create_child_agent(parent_agent, child_context, child_logger)

    assert "read_file" in child_agent.tools.list_tools()
    assert "load_skill" in child_agent.tools.list_tools()
    assert "run_readonly_command" in child_agent.tools.list_tools()
    assert "write_file" not in child_agent.tools.list_tools()
    assert "run_command" not in child_agent.tools.list_tools()
    assert "write_plan" not in child_agent.tools.list_tools()
    assert "submit_plan" not in child_agent.tools.list_tools()
    assert "run_subagent" not in child_agent.tools.list_tools()


def test_readonly_shell_allows_safe_commands_and_rejects_mutating_commands(temp_dir):
    """The read-only shell tool should allow only the configured inspection commands."""
    repo_root = temp_dir / "repo"
    repo_root.mkdir()
    (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
    session_context = Context.create(cwd=str(repo_root))
    tool = ReadOnlyShellTool()

    allowed_result = tool.execute(session_context, argv=["ls"])
    denied_result = tool.execute(session_context, argv=["python3", "-c", "print('no')"])

    assert allowed_result.success is True
    assert denied_result.success is False
    assert "not allowed in planning mode" in (denied_result.error or "")
