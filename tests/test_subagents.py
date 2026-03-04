"""Tests for local subagent runtime and agent batching."""

import json
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import time
import pytest

from src.agent import Agent
from src.commands import builtin
from src.commands.registry import CommandRegistry
from src.context import Context
from src.logger import SessionLogger
from src.skills import SkillManager
from src.subagents import (
    SubagentManager,
    SubagentRequest,
    SubagentResult,
)
from src.tools import Tool, ToolRegistry, ToolResult, build_tool_registry
from src.tools.subagent import RunSubagentTool


class DummyParentLLM:
    """Minimal parent-LLM stub used to seed child configuration."""

    def __init__(self) -> None:
        self.provider = "ollama"
        self.model = "llama3"
        self.base_url = "http://localhost:11434/v1"
        self.logger = None


class FakeChildLLM:
    """A deterministic child LLM used by subagent runtime tests."""

    def __init__(self, provider=None, model=None, base_url=None, logger=None):
        self.provider = provider or "ollama"
        self.model = model or "llama3"
        self.base_url = base_url or "http://localhost:11434/v1"
        self.logger = logger

    def chat(self, messages, tools=None, log_context=None):
        if self.logger and log_context:
            request_payload = {"model": self.model, "messages": messages, "tools": tools, "stream": False}
            self.logger.log_llm_request(
                turn_id=log_context["turn_id"],
                iteration=log_context["iteration"],
                request_payload=request_payload,
                provider=self.provider,
                model=self.model,
                stream=False,
                request_kind=log_context.get("request_kind", "agent_turn"),
            )

        metrics = SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=6,
            total_tokens=18,
            cached_tokens=0,
            duration=0.1,
            iteration=None,
        )
        response_payload = {
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "Executive summary.\n\nFull delegated report.",
                        "tool_calls": None,
                    },
                }
            ],
        }
        if self.logger and log_context:
            self.logger.log_llm_response(
                turn_id=log_context["turn_id"],
                iteration=log_context["iteration"],
                response_payload=response_payload,
                provider=self.provider,
                model=self.model,
                stream=False,
                metrics={
                    "prompt_tokens": 12,
                    "completion_tokens": 6,
                    "total_tokens": 18,
                    "cached_tokens": 0,
                    "ttft": 0.0,
                    "duration": 0.1,
                    "tokens_per_second": 60.0,
                    "tpot": 0.0,
                },
                request_kind=log_context.get("request_kind", "agent_turn"),
            )
        return {"role": "assistant", "content": "Executive summary.\n\nFull delegated report."}, metrics


class DelayedChildLLM(FakeChildLLM):
    """A child LLM that completes requests out of order while preserving result order."""

    def chat(self, messages, tools=None, log_context=None):
        task_text = messages[-1]["content"]
        if "slow-task" in task_text:
            time.sleep(0.05)
        return super().chat(messages, tools=tools, log_context=log_context)


def test_build_tool_registry_includes_and_excludes_run_subagent(temp_dir):
    """Parent registries should expose run_subagent while child ones should not."""
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager()

    parent_tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    child_tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=False,
    )

    assert "run_subagent" in parent_tools.list_tools()
    assert "run_subagent" not in child_tools.list_tools()


def test_subagent_run_creates_nested_child_session(monkeypatch, temp_dir):
    """A completed child run should return structured metadata and nested logs."""
    monkeypatch.setattr("src.subagents.LLMClient", FakeChildLLM)

    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager()
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    request = SubagentRequest(
        task="Inspect the logging flow and summarize it.",
        label="research-logging",
        context="Focus on parent and child session logging.",
        success_criteria="Return one concise report.",
        files=["src/logger.py"],
        output_hint="short report",
    )
    result = subagent_manager.run_subagents(parent_agent, [request], parent_turn_id=1)[0]
    parent_agent.logger.close()

    assert result.status == "completed"
    assert result.summary == "Executive summary."
    assert result.report.startswith("Executive summary.")
    assert Path(result.session_dir).exists()
    assert Path(result.llm_log).exists()
    assert Path(result.events_log).exists()

    session_json = json.loads((Path(result.session_dir) / "session.json").read_text())
    assert session_json["session_kind"] == "subagent"
    assert session_json["parent_session_id"] == context.session_id
    assert session_json["parent_turn_id"] == 1
    assert session_json["subagent_label"] == "research-logging"

    child_llm_log = Path(result.llm_log).read_text()
    assert "Request Kind: subagent_turn" in child_llm_log
    assert "run_subagent" not in child_llm_log

    latest_session = (Path(temp_dir) / "latest-session").resolve()
    assert latest_session == parent_agent.logger.session_dir.resolve()


def test_run_subagents_executes_multiple_real_children_in_input_order(monkeypatch, temp_dir):
    """Multiple real child runs should finish with stable input ordering and nested logs."""
    monkeypatch.setattr("src.subagents.LLMClient", DelayedChildLLM)

    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager()
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    events = []
    results = subagent_manager.run_subagents(
        parent_agent,
        [
            SubagentRequest(
                task="slow-task: inspect logger flow",
                label="slow-one",
                context="",
                success_criteria="",
                files=[],
                output_hint="",
            ),
            SubagentRequest(
                task="fast-task: inspect agent flow",
                label="fast-two",
                context="",
                success_criteria="",
                files=[],
                output_hint="",
            ),
        ],
        parent_turn_id=1,
        on_event=lambda event: events.append(event),
    )
    parent_agent.logger.close()

    assert [result.label for result in results] == ["slow-one", "fast-two"]
    assert [result.status for result in results] == ["completed", "completed"]
    assert len(subagent_manager.list_runs()) == 2
    assert all(Path(result.session_dir).exists() for result in results)
    assert [event.kind for event in events[:2]] == ["subagent_started", "subagent_started"]
    forwarded_events = [event for event in events if event.worker_kind == "subagent"]
    assert {event.worker_label for event in forwarded_events} == {"slow-one", "fast-two"}
    assert [event.kind for event in forwarded_events].count("llm_call_started") == 2
    assert [event.kind for event in forwarded_events].count("llm_call_finished") == 2
    assert [event.kind for event in forwarded_events].count("turn_completed") == 2
    assert [event.kind for event in events].count("subagent_completed") == 2


def test_run_subagents_rejects_empty_request_list(temp_dir):
    """The execution API should require at least one request."""
    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager()
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    with pytest.raises(ValueError, match="at least one request"):
        subagent_manager.run_subagents(parent_agent, [], parent_turn_id=1)

    parent_agent.logger.close()


def test_run_subagents_returns_disabled_failures_for_each_request(temp_dir):
    """Disabled subagents should fail cleanly without creating run records."""
    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager(enabled=False)
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    results = subagent_manager.run_subagents(
        parent_agent,
        [
            SubagentRequest("a", "one", "", "", [], ""),
            SubagentRequest("b", "two", "", "", [], ""),
        ],
        parent_turn_id=1,
    )
    parent_agent.logger.close()

    assert [result.status for result in results] == ["failed", "failed"]
    assert all("disabled" in (result.error or "").lower() for result in results)
    assert subagent_manager.list_runs() == []


def test_single_subagent_run_uses_thread_pool(monkeypatch, temp_dir):
    """A single subagent should still execute through ThreadPoolExecutor."""
    monkeypatch.setattr("src.subagents.LLMClient", FakeChildLLM)

    submit_calls = []

    class TrackingExecutor:
        def __init__(self, *, max_workers, thread_name_prefix):
            submit_calls.append(("init", max_workers, thread_name_prefix))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def submit(self, fn, *args, **kwargs):
            submit_calls.append(("submit", len(args)))
            future = Future()
            future.set_result(fn(*args, **kwargs))
            return future

    monkeypatch.setattr("src.subagents.ThreadPoolExecutor", TrackingExecutor)

    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager()
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    request = SubagentRequest(
        task="Explain the logger.",
        label="thread-check",
        context="",
        success_criteria="",
        files=[],
        output_hint="",
    )
    result = subagent_manager.run_subagents(parent_agent, [request], parent_turn_id=1)[0]
    parent_agent.logger.close()

    assert result.status == "completed"
    assert ("init", 1, "subagent") in submit_calls
    assert any(call[0] == "submit" for call in submit_calls)


def test_run_subagents_uses_one_worker_per_accepted_request(monkeypatch, temp_dir):
    """Accepted subagents should all be submitted in one executor batch."""
    monkeypatch.setattr("src.subagents.LLMClient", FakeChildLLM)

    submit_calls = []

    class TrackingExecutor:
        def __init__(self, *, max_workers, thread_name_prefix):
            submit_calls.append(("init", max_workers, thread_name_prefix))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def submit(self, fn, *args, **kwargs):
            submit_calls.append(("submit", len(args)))
            future = Future()
            future.set_result(fn(*args, **kwargs))
            return future

    monkeypatch.setattr("src.subagents.ThreadPoolExecutor", TrackingExecutor)

    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager(max_per_turn=6)
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    results = subagent_manager.run_subagents(
        parent_agent,
        [
            SubagentRequest("a", "one", "", "", [], ""),
            SubagentRequest("b", "two", "", "", [], ""),
            SubagentRequest("c", "three", "", "", [], ""),
        ],
        parent_turn_id=1,
    )
    parent_agent.logger.close()

    assert [result.status for result in results] == ["completed", "completed", "completed"]
    assert ("init", 3, "subagent") in submit_calls
    assert sum(1 for call in submit_calls if call[0] == "submit") == 3


def test_run_subagents_respects_max_parallel(monkeypatch, temp_dir):
    """Executor width should be capped by max_parallel even when more requests are accepted."""
    monkeypatch.setattr("src.subagents.LLMClient", FakeChildLLM)

    submit_calls = []

    class TrackingExecutor:
        def __init__(self, *, max_workers, thread_name_prefix):
            submit_calls.append(("init", max_workers, thread_name_prefix))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def submit(self, fn, *args, **kwargs):
            submit_calls.append(("submit", len(args)))
            future = Future()
            future.set_result(fn(*args, **kwargs))
            return future

    monkeypatch.setattr("src.subagents.ThreadPoolExecutor", TrackingExecutor)

    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager(max_parallel=2, max_per_turn=5)
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    results = subagent_manager.run_subagents(
        parent_agent,
        [
            SubagentRequest("a", "one", "", "", [], ""),
            SubagentRequest("b", "two", "", "", [], ""),
            SubagentRequest("c", "three", "", "", [], ""),
            SubagentRequest("d", "four", "", "", [], ""),
        ],
        parent_turn_id=1,
    )
    parent_agent.logger.close()

    assert [result.status for result in results] == ["completed", "completed", "completed", "completed"]
    assert ("init", 2, "subagent") in submit_calls


def test_run_subagents_rejects_overflow_requests_and_preserves_order(monkeypatch, temp_dir):
    """Requests beyond max_per_turn should be rejected after allowed requests keep their order."""
    monkeypatch.setattr("src.subagents.LLMClient", FakeChildLLM)

    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager(max_per_turn=2)
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    results = subagent_manager.run_subagents(
        parent_agent,
        [
            SubagentRequest("a", "one", "", "", [], ""),
            SubagentRequest("b", "two", "", "", [], ""),
            SubagentRequest("c", "three", "", "", [], ""),
        ],
        parent_turn_id=1,
    )
    parent_agent.logger.close()

    assert [result.label for result in results] == ["one", "two", "three"]
    assert [result.status for result in results] == ["completed", "completed", "failed"]
    assert "max_per_turn=2" in (results[2].error or "")
    assert len(subagent_manager.list_runs()) == 2


def test_run_subagents_enforces_per_turn_capacity_across_multiple_calls(monkeypatch, temp_dir):
    """Per-turn capacity should accumulate across repeated calls in the same parent turn."""
    monkeypatch.setattr("src.subagents.LLMClient", FakeChildLLM)

    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager(max_per_turn=2)
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    first_results = subagent_manager.run_subagents(
        parent_agent,
        [SubagentRequest("a", "one", "", "", [], "")],
        parent_turn_id=1,
    )
    second_results = subagent_manager.run_subagents(
        parent_agent,
        [
            SubagentRequest("b", "two", "", "", [], ""),
            SubagentRequest("c", "three", "", "", [], ""),
        ],
        parent_turn_id=1,
    )
    parent_agent.logger.close()

    assert [result.status for result in first_results] == ["completed"]
    assert [result.status for result in second_results] == ["completed", "failed"]
    assert second_results[1].label == "three"
    assert len(subagent_manager.list_runs()) == 2


def test_run_subagents_returns_timed_out_result(temp_dir, monkeypatch):
    """Timed-out child runs should return a structured timed_out result."""
    context = Context.create(cwd=str(temp_dir))
    skill_manager = SkillManager(repo_root=Path(temp_dir))
    skill_manager.discover()
    subagent_manager = SubagentManager(default_timeout_seconds=0.001)
    tools = build_tool_registry(
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        include_subagent_tool=True,
    )
    parent_logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    parent_agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        skill_manager=skill_manager,
        logger=parent_logger,
        subagent_manager=subagent_manager,
    )

    def slow_run_subagent_in_thread(parent_agent, prepared_subagent_run, parent_turn_id, on_event):
        time.sleep(0.05)
        return SubagentResult(
            subagent_id=prepared_subagent_run.run.subagent_id,
            label=prepared_subagent_run.run.label,
            status="completed",
            summary="late",
            report="late",
            session_dir=prepared_subagent_run.session_dir,
            llm_log=prepared_subagent_run.llm_log,
            events_log=prepared_subagent_run.events_log,
            llm_call_count=0,
            tool_call_count=0,
            tools_used=[],
        )

    monkeypatch.setattr(subagent_manager, "_run_subagent_in_thread", slow_run_subagent_in_thread)

    result = subagent_manager.run_subagents(
        parent_agent,
        [SubagentRequest("a", "one", "", "", [], "")],
        parent_turn_id=1,
    )[0]
    parent_agent.logger.close()

    assert result.status == "timed_out"
    assert "timed out" in (result.error or "")
    assert subagent_manager.get_run(result.subagent_id).status == "timed_out"


def test_agent_batches_consecutive_run_subagent_calls(temp_dir):
    """The agent should batch consecutive run_subagent tool calls through SubagentManager."""

    class FakeSubagentManager:
        def __init__(self):
            self.seen_requests = []

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
            results = []
            for index, request in enumerate(requests, start=1):
                result = SubagentResult(
                    subagent_id=f"sa_000{index}",
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
                results.append(result)
            return results

    fake_manager = FakeSubagentManager()
    context = Context.create(cwd=str(temp_dir))
    logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    tools = ToolRegistry()
    tools.register(RunSubagentTool(fake_manager))
    agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        logger=logger,
        subagent_manager=fake_manager,
    )

    turn_id = agent.logger.start_turn(raw_user_input="delegate", normalized_user_input="delegate")
    messages = []
    processed = agent._process_tool_calls(
        [
            {"id": "call_1", "name": "run_subagent", "arguments": json.dumps({"task": "a", "label": "one"})},
            {"id": "call_2", "name": "run_subagent", "arguments": json.dumps({"task": "b", "label": "two"})},
        ],
        messages,
        turn_id,
        0,
    )
    agent.logger.close()

    assert processed.processed_count == 2
    assert processed.terminal_response is None
    assert [request.task for request in fake_manager.seen_requests] == ["a", "b"]
    assert len(messages) == 2
    assert json.loads(messages[0]["content"])["subagent_id"] == "sa_0001"
    assert json.loads(messages[1]["content"])["subagent_id"] == "sa_0002"


def test_agent_preserves_tool_call_order_with_invalid_subagent_arguments(temp_dir):
    """Invalid run_subagent tool calls should stay inline while valid requests still execute."""

    class FakeSubagentManager:
        def __init__(self):
            self.seen_requests = []

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
                    subagent_id=f"sa_000{index}",
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

    fake_manager = FakeSubagentManager()
    context = Context.create(cwd=str(temp_dir))
    logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    tools = ToolRegistry()
    tools.register(RunSubagentTool(fake_manager))
    agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        logger=logger,
        subagent_manager=fake_manager,
    )

    turn_id = agent.logger.start_turn(raw_user_input="delegate", normalized_user_input="delegate")
    messages = []
    processed = agent._process_tool_calls(
        [
            {"id": "call_1", "name": "run_subagent", "arguments": json.dumps({"task": "a", "label": "one"})},
            {"id": "call_2", "name": "run_subagent", "arguments": "{bad json"},
            {"id": "call_3", "name": "run_subagent", "arguments": json.dumps({"task": "b", "label": "two"})},
        ],
        messages,
        turn_id,
        0,
    )
    agent.logger.close()

    assert processed.processed_count == 3
    assert processed.terminal_response is None
    assert [request.task for request in fake_manager.seen_requests] == ["a", "b"]
    assert json.loads(messages[0]["content"])["subagent_id"] == "sa_0001"
    assert "Invalid JSON in tool arguments" in json.loads(messages[1]["content"])["error"]
    assert json.loads(messages[2]["content"])["subagent_id"] == "sa_0002"


def test_agent_preserves_order_around_subagent_batch(temp_dir):
    """Normal tool results should stay before and after a consecutive run_subagent batch."""

    class EchoTool(Tool):
        name = "echo"
        description = "Return the provided text."
        parameters = {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
            "additionalProperties": False,
        }

        def execute(self, context, **kwargs):
            return ToolResult(success=True, data=kwargs["text"])

    class FakeSubagentManager:
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
            return [
                SubagentResult(
                    subagent_id=f"sa_000{index}",
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

    fake_manager = FakeSubagentManager()
    context = Context.create(cwd=str(temp_dir))
    logger = SessionLogger(context.session_id, log_dir=str(temp_dir), enabled=True)
    tools = ToolRegistry()
    tools.register(EchoTool())
    tools.register(RunSubagentTool(fake_manager))
    agent = Agent(
        DummyParentLLM(),
        tools,
        context,
        logger=logger,
        subagent_manager=fake_manager,
    )

    turn_id = agent.logger.start_turn(raw_user_input="delegate", normalized_user_input="delegate")
    messages = []
    processed = agent._process_tool_calls(
        [
            {"id": "call_1", "name": "echo", "arguments": json.dumps({"text": "before"})},
            {"id": "call_2", "name": "run_subagent", "arguments": json.dumps({"task": "a", "label": "one"})},
            {"id": "call_3", "name": "run_subagent", "arguments": json.dumps({"task": "b", "label": "two"})},
            {"id": "call_4", "name": "echo", "arguments": json.dumps({"text": "after"})},
        ],
        messages,
        turn_id,
        0,
    )
    agent.logger.close()

    assert processed.processed_count == 4
    assert processed.terminal_response is None
    assert [json.loads(message["content"]) for message in messages] == [
        {"output": "before"},
        {
            "subagent_id": "sa_0001",
            "label": "one",
            "status": "completed",
            "summary": "summary 1",
            "report": "report 1",
            "session_dir": "/tmp/sa_1",
            "llm_log": "/tmp/sa_1/llm.log",
            "events_log": "/tmp/sa_1/events.jsonl",
            "llm_call_count": 1,
            "tool_call_count": 0,
            "tools_used": [],
            "error": None,
        },
        {
            "subagent_id": "sa_0002",
            "label": "two",
            "status": "completed",
            "summary": "summary 2",
            "report": "report 2",
            "session_dir": "/tmp/sa_2",
            "llm_log": "/tmp/sa_2/llm.log",
            "events_log": "/tmp/sa_2/events.jsonl",
            "llm_call_count": 1,
            "tool_call_count": 0,
            "tools_used": [],
            "error": None,
        },
        {"output": "after"},
    ]


def test_builtin_subagent_command_show_and_run_help():
    """The built-in registry should expose /subagent help and targeted help."""
    registry = CommandRegistry()
    builtin.register_all(registry)

    assert registry.get_command("subagent") is not None
