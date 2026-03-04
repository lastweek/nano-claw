"""Tests for context compaction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.agent import Agent
from src.config import Config
from src.context import Context
from src.context_compaction import ContextCompactionManager, ContextCompactionPolicy
from src.tools import ToolRegistry


class StubLLM:
    """Minimal LLM stub for compaction summary generation."""

    provider = "stub"
    model = "stub-model"
    base_url = None
    logger = None

    def __init__(self, content: str = None, *, error: Exception | None = None):
        self.content = (
            "Conversation summary for earlier turns:\n\n"
            "## Goal\n"
            "- Keep the session moving\n\n"
            "## Instructions\n"
            "- none\n\n"
            "## Discoveries\n"
            "- Repo uses session logging\n\n"
            "## Accomplished\n"
            "- Reviewed the current implementation\n\n"
            "## Relevant files / directories\n"
            "- src/agent.py (message assembly lives here)\n\n"
            "This summary replaces older raw turns. Prefer recent raw turns if they conflict."
        ) if content is None else content
        self.error = error
        self.calls = []

    def chat(self, messages, tools=None, log_context=None):
        self.calls.append({"messages": messages, "tools": tools, "log_context": log_context})
        if self.error is not None:
            raise self.error
        return {"role": "assistant", "content": self.content}, SimpleNamespace()


class AgentCompactionLLM:
    """LLM stub that distinguishes compaction requests from normal agent turns."""

    provider = "stub"
    model = "stub-model"
    base_url = None
    logger = None

    def __init__(self, *, invalid_compaction_json: bool = False):
        self.invalid_compaction_json = invalid_compaction_json
        self.calls = []

    def chat(self, messages, tools=None, log_context=None):
        self.calls.append({"messages": messages, "tools": tools, "log_context": log_context})
        request_kind = (log_context or {}).get("request_kind", "agent_turn")
        if request_kind == "context_compaction":
            if self.invalid_compaction_json:
                raise RuntimeError("compaction summarizer failed")
            return {
                "role": "assistant",
                "content": (
                    "Conversation summary for earlier turns:\n\n"
                    "## Goal\n"
                    "- Continue the task\n\n"
                    "## Instructions\n"
                    "- none\n\n"
                    "## Discoveries\n"
                    "- none\n\n"
                    "## Accomplished\n"
                    "- Summarized older turns\n\n"
                    "## Relevant files / directories\n"
                    "- none\n\n"
                    "This summary replaces older raw turns. Prefer recent raw turns if they conflict."
                ),
            }, SimpleNamespace(iteration=None)

        return {"role": "assistant", "content": "final answer"}, SimpleNamespace(
            iteration=None,
            prompt_tokens=10,
            completion_tokens=3,
            total_tokens=13,
            cached_tokens=0,
            duration=0.1,
        )

    def chat_stream(self, messages, tools=None, log_context=None):
        self.calls.append({"messages": messages, "tools": tools, "log_context": log_context})
        request_kind = (log_context or {}).get("request_kind", "agent_turn")
        if request_kind == "context_compaction":
            raise AssertionError("context compaction should use non-streaming requests")

        yield {"role": "assistant"}
        yield {"delta": "final "}
        yield {"delta": "answer"}
        yield {"finish_reason": "stop"}

    def get_stream_metrics(self):
        return SimpleNamespace(
            iteration=None,
            prompt_tokens=10,
            completion_tokens=3,
            total_tokens=13,
            cached_tokens=0,
            duration=0.1,
        )

    def get_stream_tool_calls(self):
        return []


class DummyAgent:
    """Small agent-shaped object for context usage snapshots."""

    def __init__(self, llm, context):
        self.llm = llm
        self.context = context
        self.skill_manager = None
        self.tools = ToolRegistry()
        self._cached_system_prompt_base = "You are a coding assistant."
        self._cached_tool_schemas = []

    def _build_skill_catalog_section(self):
        return ""


def add_turns(context: Context, count: int, *, size: int = 80) -> None:
    """Append complete user/assistant turns to the context."""
    for index in range(count):
        context.add_message("user", f"user {index} " + ("u" * size))
        context.add_message("assistant", f"assistant {index} " + ("a" * size))


def build_manager(temp_dir, monkeypatch, *, context_window, min_recent_turns=6, llm=None):
    """Build a compaction manager with test-local config."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    cfg = Config.reload()
    cfg.llm.context_window = context_window
    cfg.context.auto_compact = True
    cfg.context.auto_compact_threshold = 0.85
    cfg.context.target_usage_after_compaction = 0.60
    cfg.context.min_recent_turns = min_recent_turns

    context = Context.create(cwd=str(temp_dir))
    llm = llm or StubLLM()
    agent = DummyAgent(llm, context)
    manager = ContextCompactionManager(
        llm,
        context,
        None,
        ContextCompactionPolicy(
            auto_compact=cfg.context.auto_compact,
            auto_compact_threshold=cfg.context.auto_compact_threshold,
            target_usage_after_compaction=cfg.context.target_usage_after_compaction,
            min_recent_turns=cfg.context.min_recent_turns,
        ),
    )
    return cfg, context, agent, manager


def test_no_compaction_below_threshold(temp_dir, monkeypatch):
    """Auto-compaction should not trigger when baseline usage is below threshold."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=100_000, min_recent_turns=2)
    add_turns(context, 4, size=20)

    decision = manager.build_decision(agent)

    assert decision.should_compact is False
    assert decision.reason == "below_threshold"


def test_compaction_triggers_at_threshold(temp_dir, monkeypatch):
    """Auto-compaction should trigger once estimated usage reaches the configured threshold."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=400, min_recent_turns=1)
    add_turns(context, 8, size=120)

    decision = manager.build_decision(agent)

    assert decision.should_compact is True
    assert decision.reason == "threshold_reached"


def test_one_turn_manual_compaction_skips(temp_dir, monkeypatch):
    """Manual compaction should skip when only one complete turn exists."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=400, min_recent_turns=6)
    add_turns(context, 1, size=120)

    result = manager.compact_now(agent, "manual_command", force=True)

    assert result.status == "skipped"
    assert result.reason == "insufficient_turns"
    assert result.retained_turn_count == 1
    assert result.details["effective_retained_turns"] == 1


def test_two_turn_manual_compaction_adapts_retention(temp_dir, monkeypatch):
    """Manual compaction should work on two turns even if configured retention is larger."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=400, min_recent_turns=6)
    add_turns(context, 2, size=120)

    result = manager.compact_now(agent, "manual_command", force=True)

    assert result.status == "compacted"
    assert result.covered_turn_count == 1
    assert result.retained_turn_count == 1
    assert len(context.get_complete_turns()) == 1


def test_small_auto_compaction_adapts_retention(temp_dir, monkeypatch):
    """Auto-compaction should adapt retention downward on smaller histories."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=250, min_recent_turns=6)
    add_turns(context, 3, size=120)

    decision = manager.build_decision(agent)

    assert decision.should_compact is True
    assert decision.reason == "threshold_reached"
    assert decision.details["effective_retained_turns"] == 2
    assert decision.details["evictable_turn_count"] == 1


def test_auto_compaction_prefers_last_prompt_metrics(temp_dir, monkeypatch):
    """Auto-compaction should use last prompt metrics when available."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=1000, min_recent_turns=2)
    add_turns(context, 2, size=10)

    context.last_prompt_tokens = 900
    context.last_context_window = 1000

    decision = manager.build_decision(agent)

    assert decision.should_compact is True
    assert decision.current_used_tokens == 900


def test_compaction_keeps_most_recent_turns_raw(temp_dir, monkeypatch):
    """Manual compaction should keep the configured number of recent turns verbatim."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=400, min_recent_turns=6)
    add_turns(context, 8, size=120)

    result = manager.compact_now(agent, "manual_command", force=True)

    assert result.status == "compacted"
    assert result.retained_turn_count == 6
    assert len(context.get_complete_turns()) == 6
    assert context.get_summary() is not None
    assert context.get_summary().covered_turn_count == 2


def test_freeform_summary_is_stored_as_is(temp_dir, monkeypatch):
    """Freeform summary output should be stored without JSON parsing."""
    summary_text = (
        "Conversation summary for earlier turns:\n\n"
        "## Goal\n"
        "- Test freeform summary\n\n"
        "## Instructions\n"
        "- none\n\n"
        "## Discoveries\n"
        "- none\n\n"
        "## Accomplished\n"
        "- none\n\n"
        "## Relevant files / directories\n"
        "- none\n\n"
        "This summary replaces older raw turns. Prefer recent raw turns if they conflict."
    )
    llm = StubLLM(content=summary_text)
    _, context, agent, manager = build_manager(
        temp_dir,
        monkeypatch,
        context_window=400,
        min_recent_turns=2,
        llm=llm,
    )
    add_turns(context, 4, size=120)

    result = manager.compact_now(agent, "manual_command", force=True)

    assert result.status == "compacted"
    assert context.get_summary() is not None
    assert context.get_summary().rendered_text == summary_text


def test_compaction_enforces_summary_boundary_in_prompt(temp_dir, monkeypatch):
    """Older raw turns should not reappear after compaction."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=400, min_recent_turns=1)
    add_turns(context, 3, size=40)

    manager.compact_now(agent, "manual_command", force=True)

    messages = context.get_messages()
    raw_contents = [
        message.get("content", "")
        for message in messages
        if message.get("role") in ("user", "assistant")
    ]
    assert all("user 0" not in content for content in raw_contents)
    assert all("assistant 0" not in content for content in raw_contents)
    assert any("user 2" in content for content in raw_contents)


def test_larger_history_keeps_configured_retention(temp_dir, monkeypatch):
    """Configured retention still applies unchanged on larger histories."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=400, min_recent_turns=6)
    add_turns(context, 10, size=120)

    decision = manager.build_decision(agent)

    assert decision.should_compact is True
    assert decision.details["effective_retained_turns"] == 6
    assert decision.details["evictable_turn_count"] == 4


def test_compaction_updates_existing_summary(temp_dir, monkeypatch):
    """Repeated compaction should extend the rolling summary rather than replace it blindly."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=400, min_recent_turns=2)
    add_turns(context, 5, size=120)

    first = manager.compact_now(agent, "manual_command", force=True)
    assert first.status == "compacted"
    add_turns(context, 2, size=120)

    second = manager.compact_now(agent, "manual_command", force=True)

    summary = context.get_summary()
    assert second.status == "compacted"
    assert summary is not None
    assert summary.compaction_count == 2
    assert summary.covered_turn_count >= first.covered_turn_count + 1


def test_malformed_tail_is_preserved(temp_dir, monkeypatch):
    """Malformed tail messages should remain raw and untouched by compaction."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=400, min_recent_turns=6)
    add_turns(context, 7, size=120)
    context.add_message("assistant", "dangling tail")

    manager.compact_now(agent, "manual_command", force=True)

    assert context.messages[-1] == {"role": "assistant", "content": "dangling tail"}
    assert len(context.get_complete_turns()) == 6


def test_fallback_summary_path_on_invalid_output(temp_dir, monkeypatch):
    """Invalid summarizer output should fall back to a deterministic emergency summary."""
    llm = StubLLM(error=RuntimeError("boom"))
    _, context, agent, manager = build_manager(
        temp_dir,
        monkeypatch,
        context_window=400,
        min_recent_turns=2,
        llm=llm,
    )
    add_turns(context, 5, size=120)

    result = manager.compact_now(agent, "manual_command", force=True)

    assert result.status == "compacted"
    assert result.error is not None
    assert "Fallback summary generated" in context.get_summary().rendered_text


def test_auto_compaction_skipped_when_context_window_unknown(temp_dir, monkeypatch):
    """Automatic compaction should not trigger without a configured context window."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=None, min_recent_turns=2)
    add_turns(context, 6, size=120)

    decision = manager.build_decision(agent)

    assert decision.should_compact is False
    assert decision.reason == "unknown_context_window"


def test_manual_compaction_works_without_context_window(temp_dir, monkeypatch):
    """Manual compaction should still work when context window is unknown."""
    _, context, agent, manager = build_manager(temp_dir, monkeypatch, context_window=None, min_recent_turns=2)
    add_turns(context, 5, size=120)

    result = manager.compact_now(agent, "manual_command", force=True)

    assert result.status == "compacted"
    assert context.get_summary() is not None
    assert len(context.get_complete_turns()) == 2


def test_agent_run_auto_compacts_before_first_normal_call(temp_dir, monkeypatch):
    """Agent.run should compact before the first normal LLM request when threshold is exceeded."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    cfg = Config.reload()
    cfg.logging.enabled = False
    cfg.llm.context_window = 400
    cfg.context.min_recent_turns = 2

    context = Context.create(cwd=str(temp_dir))
    add_turns(context, 5, size=120)
    llm = AgentCompactionLLM()
    agent = Agent(llm, ToolRegistry(), context)

    events = []
    response = agent.run("continue", on_event=lambda event: events.append(event))

    assert response == "final answer"
    assert llm.calls[0]["log_context"]["request_kind"] == "context_compaction"
    assert llm.calls[1]["log_context"]["request_kind"] == "agent_turn"
    assert "Conversation summary for earlier turns:" in llm.calls[1]["messages"][1]["content"]
    assert [event.kind for event in events[:3]] == [
        "context_compaction_started",
        "context_compaction_completed",
        "llm_call_started",
    ]


def test_agent_run_stream_auto_compacts_before_streaming(temp_dir, monkeypatch):
    """Agent.run_stream should compact before the first streamed LLM request."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    cfg = Config.reload()
    cfg.logging.enabled = False
    cfg.llm.context_window = 400
    cfg.context.min_recent_turns = 2

    context = Context.create(cwd=str(temp_dir))
    add_turns(context, 5, size=120)
    llm = AgentCompactionLLM()
    agent = Agent(llm, ToolRegistry(), context)

    events = []
    tokens = list(agent.run_stream("continue", on_event=lambda event: events.append(event)))

    assert "".join(chunk for chunk in tokens if isinstance(chunk, str)) == "final answer"
    assert llm.calls[0]["log_context"]["request_kind"] == "context_compaction"
    assert llm.calls[1]["log_context"]["request_kind"] == "agent_turn"
    assert any(event.kind == "context_compaction_completed" for event in events)


def test_agent_compaction_failure_falls_back_and_turn_continues(temp_dir, monkeypatch):
    """Fallback summaries should still let the turn proceed when summarization output is invalid."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    cfg = Config.reload()
    cfg.logging.enabled = False
    cfg.llm.context_window = 400
    cfg.context.min_recent_turns = 2

    context = Context.create(cwd=str(temp_dir))
    add_turns(context, 5, size=120)
    llm = AgentCompactionLLM(invalid_compaction_json=True)
    agent = Agent(llm, ToolRegistry(), context)

    events = []
    response = agent.run("continue", on_event=lambda event: events.append(event))

    assert response == "final answer"
    assert any(event.kind == "context_compaction_failed" for event in events)
    assert context.get_summary() is not None
    assert "Fallback summary generated" in context.get_summary().rendered_text
