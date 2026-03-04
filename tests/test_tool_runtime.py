"""Tests for the extracted agent tool runtime."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.context import Context
from src.plan_mode import create_session_plan, write_plan_content
from src.subagents import SubagentRequest, SubagentResult
from src.tool_runtime import AgentToolRuntime
from src.tools import Tool, ToolRegistry, ToolResult
from src.tools.plan_submit import SubmitPlanTool
from src.tools.subagent import RunSubagentTool


class FakeLogger:
    """Small logger stub for tool-runtime unit tests."""

    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.tool_results: list[dict] = []
        self.plan_events: list[dict] = []

    def log_tool_call(self, **kwargs) -> None:
        self.tool_calls.append(kwargs)

    def log_tool_result(self, **kwargs) -> None:
        self.tool_results.append(kwargs)

    def log_plan_event(self, **kwargs) -> None:
        self.plan_events.append(kwargs)


def create_tool_runtime(context: Context, registry: ToolRegistry, *, subagent_manager=None):
    """Build a runtime with deterministic helper callbacks."""
    turn_events: list[tuple[str, dict]] = []
    skill_events: list[tuple[str, dict]] = []
    logger = FakeLogger()

    def build_tool_result_message(tool_id: str, result: dict) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_id,
            "content": json.dumps(result),
        }

    def parse_tool_arguments_for_logging(raw_arguments: str) -> dict:
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            return {"raw_arguments": raw_arguments, "parse_error": str(exc)}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def emit_turn_event(callback, kind: str, *, iteration=None, **details) -> None:
        turn_events.append((kind, details))

    def emit_skill_event(turn_id: int, event: str, **details) -> None:
        skill_events.append((event, details))

    runtime = AgentToolRuntime(
        parent_agent=SimpleNamespace(),
        context=context,
        logger=logger,
        subagent_manager=subagent_manager,
        get_tool=registry.get,
        build_tool_result_message=build_tool_result_message,
        parse_tool_arguments_for_logging=parse_tool_arguments_for_logging,
        emit_turn_event=emit_turn_event,
        emit_skill_event=emit_skill_event,
    )
    return runtime, logger, turn_events, skill_events


class EchoTool(Tool):
    """Simple deterministic tool for ordinary tool runtime tests."""

    name = "echo"
    description = "Return the provided text."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    def execute(self, context, **kwargs):
        return ToolResult(success=True, data=kwargs["text"])


class FakeLoadSkillTool(Tool):
    """A load_skill shim that lets the runtime emit skill lifecycle events."""

    name = "load_skill"
    description = "Load a named skill."
    parameters = {
        "type": "object",
        "properties": {"skill_name": {"type": "string"}},
        "required": ["skill_name"],
        "additionalProperties": False,
    }

    def execute(self, context, **kwargs):
        return ToolResult(success=True, data=f"loaded {kwargs['skill_name']}")


def test_process_tool_calls_executes_standard_tools_and_appends_messages(tmp_path):
    """Ordinary tools should still execute through the extracted runtime."""
    context = Context.create(cwd=str(tmp_path))
    registry = ToolRegistry()
    registry.register(EchoTool())
    runtime, logger, turn_events, _ = create_tool_runtime(context, registry)
    messages: list[dict] = []

    outcome = runtime.process_tool_calls(
        [{"id": "call-1", "name": "echo", "arguments": json.dumps({"text": "hello"})}],
        messages=messages,
        turn_id=1,
        iteration=0,
        tools_used=[],
        skills_used=[],
    )

    assert outcome.processed_count == 1
    assert outcome.terminal_response is None
    assert json.loads(messages[0]["content"]) == {"output": "hello"}
    assert logger.tool_calls[0]["tool_name"] == "echo"
    assert logger.tool_results[0]["tool_name"] == "echo"
    assert [event[0] for event in turn_events] == ["tool_call_started", "tool_call_finished"]


def test_process_tool_calls_keeps_submit_plan_as_terminal_control_tool(tmp_path):
    """submit_plan should end the tool batch with a terminal planning report."""
    context = Context.create(cwd=str(tmp_path))
    create_session_plan(context, task="Plan the cleanup", plan_dir=".nano-claw/plans")
    write_plan_content(context, "# Plan\n\n- inspect\n- refactor\n- verify\n")
    registry = ToolRegistry()
    registry.register(SubmitPlanTool())
    runtime, logger, turn_events, _ = create_tool_runtime(context, registry)
    messages: list[dict] = []

    outcome = runtime.process_tool_calls(
        [
            {
                "id": "call-1",
                "name": "submit_plan",
                "arguments": json.dumps(
                    {
                        "summary": "Inspect, refactor, verify",
                        "report": "Planning report.\n\nProceed carefully.",
                    }
                ),
            }
        ],
        messages=messages,
        turn_id=1,
        iteration=0,
        tools_used=[],
        skills_used=[],
    )

    assert outcome.processed_count == 1
    assert outcome.terminal_response == "Planning report.\n\nProceed carefully."
    assert messages == []
    assert context.get_current_plan().status == "ready_for_review"
    assert logger.plan_events[-1]["stage"] == "submitted"
    assert turn_events[-1][0] == "plan_submitted"


def test_process_subagent_batch_preserves_inline_error_order(tmp_path):
    """Batched subagent calls should preserve input order around invalid arguments."""

    class FakeSubagentManager:
        def __init__(self) -> None:
            self.seen_requests: list[SubagentRequest] = []

        def build_subagent_request(self, arguments):
            return SubagentRequest(
                task=arguments["task"],
                label=arguments.get("label", ""),
                context="",
                success_criteria="",
                files=[],
                output_hint="",
            )

        def run_subagents(self, parent_agent, requests, *, parent_turn_id, on_event=None):
            self.seen_requests = list(requests)
            return [
                SubagentResult(
                    subagent_id=f"sa_{index}",
                    label=request.label or f"subagent-{index}",
                    status="completed",
                    summary=f"summary {index}",
                    report=f"report {index}",
                    session_dir=f"/tmp/sa_{index}",
                    llm_log=f"/tmp/sa_{index}/llm.log",
                    events_log=f"/tmp/sa_{index}/events.jsonl",
                    llm_call_count=1,
                    tool_call_count=0,
                    tools_used=[],
                )
                for index, request in enumerate(requests, start=1)
            ]

    context = Context.create(cwd=str(tmp_path))
    fake_manager = FakeSubagentManager()
    registry = ToolRegistry()
    registry.register(RunSubagentTool(fake_manager))
    runtime, _, _, _ = create_tool_runtime(context, registry, subagent_manager=fake_manager)
    messages: list[dict] = []

    outcome = runtime.process_tool_calls(
        [
            {"id": "call-1", "name": "run_subagent", "arguments": json.dumps({"task": "a", "label": "one"})},
            {"id": "call-2", "name": "run_subagent", "arguments": "{bad json"},
            {"id": "call-3", "name": "run_subagent", "arguments": json.dumps({"task": "b", "label": "two"})},
        ],
        messages=messages,
        turn_id=1,
        iteration=0,
        tools_used=[],
        skills_used=[],
    )

    assert outcome.processed_count == 3
    assert [request.task for request in fake_manager.seen_requests] == ["a", "b"]
    assert json.loads(messages[0]["content"])["subagent_id"] == "sa_1"
    assert "Invalid JSON in tool arguments" in json.loads(messages[1]["content"])["error"]
    assert json.loads(messages[2]["content"])["subagent_id"] == "sa_2"


def test_process_tool_calls_emits_skill_events_for_load_skill(tmp_path):
    """load_skill should keep emitting the same skill lifecycle callbacks."""
    context = Context.create(cwd=str(tmp_path))
    registry = ToolRegistry()
    registry.register(FakeLoadSkillTool())
    runtime, _, turn_events, skill_events = create_tool_runtime(context, registry)
    messages: list[dict] = []
    skills_used: list[str] = []

    outcome = runtime.process_tool_calls(
        [
            {
                "id": "call-1",
                "name": "load_skill",
                "arguments": json.dumps({"skill_name": "debugging"}),
            }
        ],
        messages=messages,
        turn_id=1,
        iteration=0,
        tools_used=[],
        skills_used=skills_used,
    )

    assert outcome.processed_count == 1
    assert skills_used == ["debugging"]
    assert [event[0] for event in skill_events] == ["tool_load_requested", "tool_load_succeeded"]
    assert [event[0] for event in turn_events if event[0].startswith("skill_")] == [
        "skill_load_requested",
        "skill_load_succeeded",
    ]
