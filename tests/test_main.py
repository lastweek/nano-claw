"""Test main.py functions."""

import pytest
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from rich.console import Console
from src.turn_activity import TurnActivityEvent

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def make_recording_console() -> Console:
    """Create a console suitable for deterministic output assertions."""
    return Console(record=True, force_terminal=False, width=100)


def normalize_output(output: str) -> str:
    """Normalize Rich exported text for stable assertions."""
    return "\n".join(line.rstrip() for line in output.splitlines())


def make_runtime_config(temp_dir: Path, **overrides):
    """Build a deterministic CLI runtime config for startup tests."""
    from src.config import Config

    payload = {
        "llm": {
            "provider": "ollama",
            "model": "fake-model",
            "base_url": "http://localhost:11434/v1",
        },
        "logging": {
            "enabled": True,
            "async_mode": False,
            "log_dir": str(temp_dir / "sessions"),
            "buffer_size": 1,
        },
        "memory": {
            "enabled": False,
            "root_dir": str(temp_dir / "sessions"),
            "auto_load_memory": True,
            "max_auto_chars": 4000,
            "max_search_results": 10,
            "default_read_policy": "curated_plus_recent_daily",
            "default_prompt_policy": "curated_plus_recent_daily",
            "recent_daily_days": 2,
            "max_auto_curated_hits": 3,
            "max_auto_daily_hits": 2,
        },
        "mcp": {"servers": []},
        "subagents": {
            "enabled": True,
            "max_parallel": 1,
            "max_per_turn": 1,
            "default_timeout_seconds": 60,
        },
        "web_tools": {
            "enabled": True,
            "timeout_seconds": 15,
            "max_response_bytes": 2_000_000,
            "max_content_chars": 20_000,
            "allow_private_networks": False,
            "enable_fetch_url": True,
            "enable_read_webpage": True,
            "enable_extract_page_links": True,
        },
    }
    for key, value in overrides.items():
        payload[key] = value
    return Config(payload)


class StubLLMClient:
    """Minimal LLM client stub for startup/runtime assembly tests."""

    def __init__(self, runtime_config=None):
        runtime_config = runtime_config or {}
        llm_config = getattr(runtime_config, "llm", None)
        self.provider = getattr(llm_config, "provider", "ollama")
        self.model = getattr(llm_config, "model", "fake-model")
        self.base_url = getattr(llm_config, "base_url", "http://localhost:11434/v1")
        self.logger = None


def make_metrics(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration: float = 0.0,
    ttft: float = 0.0,
    token_count: int = 0,
    tpot: float = 0.0,
    request_type: str = "",
    model: str = "glm-5",
    provider: str = "custom",
):
    """Create LLMMetrics with deterministic derived duration and TPOT."""
    from src.metrics import LLMMetrics

    metrics = LLMMetrics(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        model=model,
        provider=provider,
        request_type=request_type,
        ttft=ttft,
        token_count=token_count,
    )
    metrics.start_time = 100.0
    metrics.end_time = 100.0 + duration
    if token_count >= 2 and tpot > 0:
        metrics.first_token_time = 101.0
        metrics.last_token_time = metrics.first_token_time + (tpot * (token_count - 1))
    return metrics


def test_print_banner_no_error():
    """Test that print_banner runs without errors."""
    from src.main import NANO_CODER_WORDMARK, print_banner

    console = make_recording_console()
    print_banner(console)

    rendered = normalize_output(console.export_text())
    assert NANO_CODER_WORDMARK.splitlines()[0] in rendered
    assert "Minimalism Terminal Code Agent" in rendered


def test_print_banner_with_context_window(monkeypatch):
    """Test banner displays with context_window set."""
    # Enable test mode
    monkeypatch.setenv("NANO_CODER_TEST", "true")

    # Set test values
    monkeypatch.setenv("LLM_PROVIDER", "test-provider")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://test.example.com")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "128000")

    # Reload config to pick up env vars
    from src.config import Config
    Config.reload()

    # Import after reload to get fresh config
    from src.main import print_banner

    console = Console()
    # Should not raise any exceptions
    print_banner(console)


def test_print_banner_minimal_config(monkeypatch):
    """Test banner with minimal config."""
    # Enable test mode
    monkeypatch.setenv("NANO_CODER_TEST", "true")

    # Clear any existing LLM env vars
    for key in list(os.environ.keys()):
        if key.startswith(("LLM_", "API_KEY")):
            monkeypatch.delenv(key, raising=False)

    # Reload config to pick up changes
    from src.config import Config
    Config.reload()

    # Import after reload
    from src.main import print_banner

    console = Console()
    # Should not raise any exceptions even with minimal config
    print_banner(console)


def test_config_reload_is_silent(monkeypatch, capsys):
    """Config reload should not print directly during import/runtime loading."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    from src.config import Config

    Config.reload()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_load_runtime_config_prints_diagnostics_via_console(monkeypatch):
    """Bootstrap should surface config load diagnostics through the CLI console."""
    from src.main import load_runtime_config

    monkeypatch.setenv("NANO_CODER_TEST", "true")
    console = make_recording_console()
    runtime_config = load_runtime_config(console)

    assert runtime_config is not None
    rendered = normalize_output(console.export_text())
    assert "Test mode: Skipping config.yaml" in rendered


def test_display_metrics_streaming_single_call_shows_ttft_and_tpot():
    """Single streaming calls should continue showing both TTFT and TPOT."""
    from src.main import display_metrics, REQUEST_TYPE_STREAMING

    console = make_recording_console()
    metrics_list = [
        make_metrics(
            prompt_tokens=120,
            completion_tokens=10,
            duration=5.5,
            ttft=1.2,
            token_count=5,
            tpot=0.4,
            request_type=REQUEST_TYPE_STREAMING,
        )
    ]

    display_metrics(console, metrics_list, REQUEST_TYPE_STREAMING)
    normalized = normalize_output(console.export_text())

    assert "120 prompt tokens" in normalized
    assert "10 completion tokens" in normalized
    assert "5.50s" in normalized
    assert "TTFT 1.20s" in normalized
    assert "TPOT 0.40s" in normalized
    assert "glm-5 (custom)" in normalized


def test_display_metrics_streaming_uses_first_stream_ttft_and_aggregate_tpot():
    """Multi-call streaming turns should keep first-stream TTFT but aggregate TPOT."""
    from src.main import display_metrics, REQUEST_TYPE_NON_STREAMING, REQUEST_TYPE_STREAMING

    console = make_recording_console()
    metrics_list = [
        make_metrics(
            prompt_tokens=100,
            completion_tokens=5,
            duration=10.0,
            ttft=2.5,
            token_count=1,
            tpot=0.0,
            request_type=REQUEST_TYPE_STREAMING,
        ),
        make_metrics(
            prompt_tokens=50,
            completion_tokens=20,
            duration=15.0,
            ttft=0.9,
            token_count=6,
            tpot=0.3,
            request_type=REQUEST_TYPE_STREAMING,
        ),
        make_metrics(
            prompt_tokens=30,
            completion_tokens=7,
            duration=4.0,
            request_type=REQUEST_TYPE_NON_STREAMING,
        ),
    ]

    display_metrics(console, metrics_list, REQUEST_TYPE_STREAMING)
    normalized = normalize_output(console.export_text())

    assert "180 prompt tokens" in normalized
    assert "32 completion tokens" in normalized
    assert "29.00s" in normalized
    assert "TTFT 2.50s" in normalized
    assert "TPOT 0.30s" in normalized
    assert "TPOT 0.00s" not in normalized


def test_display_metrics_streaming_omits_tpot_without_valid_stream_contributors():
    """Streaming turns should omit TPOT when no streamed request has usable output timing."""
    from src.main import display_metrics, REQUEST_TYPE_STREAMING

    console = make_recording_console()
    metrics_list = [
        make_metrics(
            prompt_tokens=40,
            completion_tokens=3,
            duration=3.0,
            ttft=0.8,
            token_count=1,
            tpot=0.0,
            request_type=REQUEST_TYPE_STREAMING,
        ),
        make_metrics(
            prompt_tokens=20,
            completion_tokens=0,
            duration=1.0,
            ttft=0.0,
            token_count=0,
            tpot=0.0,
            request_type=REQUEST_TYPE_STREAMING,
        ),
    ]

    display_metrics(console, metrics_list, REQUEST_TYPE_STREAMING)
    normalized = normalize_output(console.export_text())

    assert "TTFT 0.80s" in normalized
    assert "TPOT" not in normalized


def test_display_metrics_non_streaming_omits_ttft_and_tpot():
    """Non-streaming summaries should keep omitting TTFT and TPOT."""
    from src.main import display_metrics, REQUEST_TYPE_NON_STREAMING

    console = make_recording_console()
    metrics_list = [
        make_metrics(
            prompt_tokens=75,
            completion_tokens=9,
            duration=8.25,
            request_type=REQUEST_TYPE_NON_STREAMING,
        )
    ]

    display_metrics(console, metrics_list, REQUEST_TYPE_NON_STREAMING)
    normalized = normalize_output(console.export_text())

    assert "75 prompt tokens" in normalized
    assert "9 completion tokens" in normalized
    assert "8.25s" in normalized
    assert "TTFT" not in normalized
    assert "TPOT" not in normalized


class StubContext:
    """Simple context stub for turn runner tests."""

    def __init__(self, messages=None):
        self._messages = messages or []

    def get_messages(self):
        return list(self._messages)


def test_run_agent_turn_streaming_shows_summary_and_answer():
    """Streaming turns should persist a concise summary and render the final answer once."""
    from src.main import run_agent_turn

    class StubAgent:
        def __init__(self):
            self.context = StubContext([{"role": "assistant", "content": "## Answer\n\n- one"}])

        def run_stream(self, user_input, on_tool_call=None, on_event=None):
            assert user_input == "hello"
            on_event(TurnActivityEvent("llm_call_started", iteration=0, details={
                "stream": True,
                "message_count": 2,
                "tool_schema_count": 4,
            }))
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": True,
                "duration_s": 0.2,
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
                "cached_tokens": 10,
                "has_tool_calls": True,
                "tool_call_count": 1,
                "result_kind": "tool_calls",
            }))
            on_event(TurnActivityEvent("tool_call_started", iteration=0, details={
                "tool_name": "deepwiki:ask_question",
                "tool_call_id": "call_1",
                "arguments": {
                    "repoName": "repo",
                    "question": "what?",
                },
            }))
            on_event(TurnActivityEvent("tool_call_finished", iteration=0, details={
                "tool_name": "deepwiki:ask_question",
                "tool_call_id": "call_1",
                "arguments": {
                    "repoName": "repo",
                    "question": "what?",
                },
                "success": True,
                "duration_s": 12.73,
            }))
            on_event(TurnActivityEvent("llm_call_started", iteration=1, details={
                "stream": True,
                "message_count": 4,
                "tool_schema_count": 4,
            }))
            yield "## Answer\n\n- one"
            on_event(TurnActivityEvent("llm_call_finished", iteration=1, details={
                "stream": True,
                "duration_s": 0.4,
                "prompt_tokens": 30,
                "completion_tokens": 7,
                "total_tokens": 37,
                "cached_tokens": 12,
                "has_tool_calls": False,
                "tool_call_count": 0,
                "result_kind": "final_answer",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "completed",
                "llm_call_count": 2,
                "tool_call_count": 1,
                "tools_used": ["deepwiki:ask_question"],
                "skills_used": [],
            }))

    console = make_recording_console()
    response = run_agent_turn(
        console,
        StubAgent(),
        "hello",
        enable_streaming=True,
        skill_debug=False,
    )
    normalized = normalize_output(console.export_text())

    assert response == "## Answer\n\n- one"
    assert "LLM call 1 requested 1 tool" in normalized
    assert "Tool finished: deepwiki:ask_question(repoName='repo', question='what?') (12.73s)" in normalized
    assert "LLM call 2 produced final answer" in normalized
    assert normalized.count("LLM call 1 requested 1 tool") == 1
    assert normalized.count("Tool finished: deepwiki:ask_question(repoName='repo', question='what?') (12.73s)") == 1
    assert normalized.count("LLM call 2 produced final answer") == 1
    assert "Answer" in normalized
    assert "• one" in normalized
    assert normalized.count("Answer") == 1
    assert "→ deepwiki:ask_question" not in normalized
    assert normalized.index("LLM call 2 produced final answer") < normalized.index("Answer")


def test_run_agent_turn_non_streaming_persists_summary_and_answer():
    """Non-streaming turns should use the same live activity path."""
    from src.main import run_agent_turn

    class StubAgent:
        context = StubContext()

        def run(self, user_input, on_tool_call=None, on_event=None):
            assert user_input == "hello"
            on_event(TurnActivityEvent("llm_call_started", iteration=0, details={
                "stream": False,
                "message_count": 2,
                "tool_schema_count": 4,
            }))
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": False,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 4,
                "has_tool_calls": False,
                "tool_call_count": 0,
                "result_kind": "final_answer",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "completed",
                "llm_call_count": 1,
                "tool_call_count": 0,
                "tools_used": [],
                "skills_used": [],
            }))
            return "Plain answer"

    console = make_recording_console()
    response = run_agent_turn(
        console,
        StubAgent(),
        "hello",
        enable_streaming=False,
        skill_debug=False,
    )
    normalized = normalize_output(console.export_text())

    assert response == "Plain answer"
    assert "LLM call 1 produced final answer" in normalized
    assert normalized.count("LLM call 1 produced final answer") == 1
    assert "Plain answer" in normalized
    assert normalized.count("Plain answer") == 1
    assert "Thinking..." not in normalized


def test_run_agent_turn_includes_skill_preload_summary():
    """Skill preload events should be visible in the persisted summary."""
    from src.main import run_agent_turn

    class StubAgent:
        context = StubContext()

        def run(self, user_input, on_tool_call=None, on_event=None):
            on_event(TurnActivityEvent("skill_preload", details={
                "skill_name": "pdf",
                "reason": "explicit",
                "source": "user",
                "catalog_visible": True,
            }))
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": False,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 4,
                "has_tool_calls": False,
                "tool_call_count": 0,
                "result_kind": "final_answer",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "completed",
                "llm_call_count": 1,
                "tool_call_count": 0,
                "tools_used": [],
                "skills_used": ["pdf"],
            }))
            return "Done"

    console = make_recording_console()
    run_agent_turn(
        console,
        StubAgent(),
        "hello",
        enable_streaming=False,
        skill_debug=False,
    )
    normalized = normalize_output(console.export_text())
    assert "Skill preloaded: pdf (explicit)" in normalized


def test_run_agent_turn_includes_subagent_summary_lines():
    """Subagent lifecycle events should be summarized without generic tool noise."""
    from src.main import run_agent_turn

    class StubAgent:
        context = StubContext()

        def run(self, user_input, on_tool_call=None, on_event=None):
            on_event(TurnActivityEvent("subagent_started", details={
                "subagent_id": "sa_0001_abcd1234",
                "label": "research-logging",
                "task": "inspect logging",
            }))
            on_event(TurnActivityEvent("subagent_completed", details={
                "subagent_id": "sa_0001_abcd1234",
                "label": "research-logging",
                "duration_s": 1.25,
                "summary": "summary",
            }))
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": False,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 4,
                "has_tool_calls": False,
                "tool_call_count": 0,
                "result_kind": "final_answer",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "completed",
                "llm_call_count": 1,
                "tool_call_count": 1,
                "tools_used": ["run_subagent"],
                "skills_used": [],
            }))
            return "Plain answer"

    console = make_recording_console()
    response = run_agent_turn(
        console,
        StubAgent(),
        "hello",
        enable_streaming=False,
        skill_debug=False,
    )
    normalized = normalize_output(console.export_text())

    assert response == "Plain answer"
    assert "Subagent finished: research-logging" in normalized
    assert normalized.count("Subagent finished: research-logging") == 1
    assert "Tool finished: run_subagent" not in normalized


def test_run_agent_turn_includes_compaction_summary():
    """Context compaction should appear in the persisted CLI summary."""
    from src.main import run_agent_turn

    class StubAgent:
        context = StubContext()

        def run(self, user_input, on_tool_call=None, on_event=None):
            on_event(TurnActivityEvent("context_compaction_started", details={
                "reason": "threshold_reached",
                "covered_turn_count": 4,
                "retained_turn_count": 2,
            }))
            on_event(TurnActivityEvent("context_compaction_completed", details={
                "reason": "threshold_reached",
                "covered_turn_count": 4,
                "retained_turn_count": 2,
                "before_tokens": 180000,
                "after_tokens": 110000,
            }))
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": False,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 4,
                "has_tool_calls": False,
                "tool_call_count": 0,
                "result_kind": "final_answer",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "completed",
                "llm_call_count": 1,
                "tool_call_count": 0,
                "tools_used": [],
                "skills_used": [],
            }))
            return "Done"

    console = make_recording_console()
    run_agent_turn(
        console,
        StubAgent(),
        "hello",
        enable_streaming=False,
        skill_debug=False,
    )
    normalized = normalize_output(console.export_text())
    assert "Context compacted: 4 older turns summarized" in normalized
    assert normalized.count("Context compacted: 4 older turns summarized") == 1


def test_run_agent_turn_shows_tool_failure_clearly():
    """Failed tools should remain visible in the persisted summary."""
    from src.main import run_agent_turn

    class StubAgent:
        context = StubContext()

        def run(self, user_input, on_tool_call=None, on_event=None):
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": False,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 4,
                "has_tool_calls": True,
                "tool_call_count": 1,
                "result_kind": "tool_calls",
            }))
            on_event(TurnActivityEvent("tool_call_finished", iteration=0, details={
                "tool_name": "deepwiki:ask_question",
                "tool_call_id": "call_1",
                "arguments": {
                    "repoName": "repo",
                    "question": "what?",
                },
                "success": False,
                "duration_s": 0.5,
                "error": "upstream failure",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "error",
                "llm_call_count": 1,
                "tool_call_count": 1,
                "tools_used": ["deepwiki:ask_question"],
                "skills_used": [],
            }))
            return "Sorry."

    console = make_recording_console()
    run_agent_turn(
        console,
        StubAgent(),
        "hello",
        enable_streaming=False,
        skill_debug=False,
    )
    normalized = normalize_output(console.export_text())
    assert "Tool failed: deepwiki:ask_question(repoName='repo', question='what?') (0.50s)" in normalized
    assert normalized.count("Tool failed: deepwiki:ask_question(repoName='repo', question='what?') (0.50s)") == 1


def test_run_agent_turn_distinguishes_repeated_tools_by_arguments():
    """Tool summary lines should include compact argument details."""
    from src.main import run_agent_turn

    class StubAgent:
        context = StubContext()

        def run(self, user_input, on_tool_call=None, on_event=None):
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": False,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 4,
                "has_tool_calls": True,
                "tool_call_count": 2,
                "result_kind": "tool_calls",
            }))
            on_event(TurnActivityEvent("tool_call_finished", iteration=0, details={
                "tool_name": "run_command",
                "tool_call_id": "call_1",
                "arguments": {"cmd": "pwd"},
                "success": True,
                "duration_s": 0.04,
            }))
            on_event(TurnActivityEvent("tool_call_finished", iteration=0, details={
                "tool_name": "run_command",
                "tool_call_id": "call_2",
                "arguments": {"cmd": "ls src"},
                "success": True,
                "duration_s": 0.01,
            }))
            on_event(TurnActivityEvent("llm_call_finished", iteration=1, details={
                "stream": False,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 4,
                "has_tool_calls": False,
                "tool_call_count": 0,
                "result_kind": "final_answer",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "completed",
                "llm_call_count": 2,
                "tool_call_count": 2,
                "tools_used": ["run_command"],
                "skills_used": [],
            }))
            return "Done"

    console = make_recording_console()
    run_agent_turn(
        console,
        StubAgent(),
        "hello",
        enable_streaming=False,
        skill_debug=False,
    )
    normalized = normalize_output(console.export_text())
    assert "Tool finished: run_command(cmd='pwd') (0.04s)" in normalized
    assert "Tool finished: run_command(cmd='ls src') (0.01s)" in normalized


def test_run_agent_turn_streaming_does_not_show_answer_twice():
    """Streaming turns should not keep a preview copy plus the final answer."""
    from src.main import run_agent_turn

    class StubAgent:
        def __init__(self):
            self.context = StubContext([{"role": "assistant", "content": "Plain final answer"}])

        def run_stream(self, user_input, on_tool_call=None, on_event=None):
            on_event(TurnActivityEvent("llm_call_started", iteration=0, details={
                "stream": True,
                "message_count": 2,
                "tool_schema_count": 0,
            }))
            yield "Plain "
            yield "final "
            yield "answer"
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": True,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
                "cached_tokens": 0,
                "has_tool_calls": False,
                "tool_call_count": 0,
                "result_kind": "final_answer",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "completed",
                "llm_call_count": 1,
                "tool_call_count": 0,
                "tools_used": [],
                "skills_used": [],
            }))

    console = make_recording_console()
    run_agent_turn(
        console,
        StubAgent(),
        "hello",
        enable_streaming=True,
        skill_debug=False,
    )
    normalized = normalize_output(console.export_text())

    assert normalized.count("Plain final answer") == 1


def test_run_agent_turn_enables_live_auto_refresh_in_terminal():
    """Terminal consoles should enable auto-refresh so the spinner animates."""
    from src.main import run_agent_turn

    live_kwargs = {}

    class StubLive:
        def __init__(self, *args, **kwargs):
            live_kwargs.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def update(self, renderable, refresh=False):
            return None

    class StubAgent:
        context = StubContext()

        def run(self, user_input, on_tool_call=None, on_event=None):
            on_event(TurnActivityEvent("llm_call_started", iteration=0, details={
                "stream": False,
                "message_count": 2,
                "tool_schema_count": 1,
            }))
            on_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
                "stream": False,
                "duration_s": 0.1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 0,
                "has_tool_calls": False,
                "tool_call_count": 0,
                "result_kind": "final_answer",
            }))
            on_event(TurnActivityEvent("turn_completed", details={
                "status": "completed",
                "llm_call_count": 1,
                "tool_call_count": 0,
                "tools_used": [],
                "skills_used": [],
            }))
            return "Done"

    console = Console(record=True, force_terminal=True, width=100)
    with patch("src.main.Live", StubLive):
        run_agent_turn(
            console,
            StubAgent(),
            "hello",
            enable_streaming=False,
            skill_debug=False,
        )

    assert live_kwargs["auto_refresh"] is True
    assert live_kwargs["refresh_per_second"] == 12


def test_build_agent_runtime_prints_tool_debug_report_on_darwin(temp_dir, monkeypatch):
    """CLI startup should report macOS tools as registered by default on Darwin."""
    from src.main import build_agent_runtime

    monkeypatch.setattr("src.main.LLMClient", StubLLMClient)
    monkeypatch.delenv("MACOS_TOOLS_ENABLED", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_FINDER", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_CALENDAR", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_NOTES", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_REMINDERS", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_MESSAGES", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    runtime_config = make_runtime_config(temp_dir)
    console = make_recording_console()

    runtime = build_agent_runtime(
        console,
        runtime_config,
        temp_dir,
        skill_debug=False,
        tool_debug=True,
    )
    try:
        normalized = normalize_output(console.export_text())
        assert "[TOOL] Debug mode enabled" in normalized
        assert "[TOOL] profile=build" in normalized
        assert "[TOOL] registered tools:" in normalized
        assert "[TOOL] macos_tools: registered" in normalized
        assert "[TOOL] web_tools: registered" in normalized
        assert "[TOOL] capability_tools: registered" in normalized
        assert "[TOOL] finder_action: registered" in normalized
        assert "[TOOL] calendar_action: registered" in normalized
        assert "[TOOL] notes_action: registered" in normalized
        assert "[TOOL] reminders_action: registered" in normalized
        assert "[TOOL] messages_action: registered" in normalized
        assert "[TOOL] fetch_url: registered" in normalized
        assert "[TOOL] read_webpage: registered" in normalized
        assert "[TOOL] extract_page_links: registered" in normalized
        assert "[TOOL] find_capabilities: registered" in normalized
        assert "[TOOL] request_capability: registered" in normalized
    finally:
        runtime.agent.logger.close()
        if runtime.mcp_manager:
            runtime.mcp_manager.close_all()


def test_build_agent_runtime_prints_tool_debug_platform_skip(temp_dir, monkeypatch):
    """CLI startup should report platform-based macOS tool skips on unsupported OSes."""
    from src.main import build_agent_runtime

    monkeypatch.setattr("src.main.LLMClient", StubLLMClient)
    monkeypatch.delenv("MACOS_TOOLS_ENABLED", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    runtime_config = make_runtime_config(temp_dir)
    console = make_recording_console()

    runtime = build_agent_runtime(
        console,
        runtime_config,
        temp_dir,
        skill_debug=False,
        tool_debug=True,
    )
    try:
        normalized = normalize_output(console.export_text())
        assert "[TOOL] macos_tools: skipped: platform is linux, requires darwin" in normalized
        assert "[TOOL] web_tools: registered" in normalized
        assert "[TOOL] capability_tools: registered" in normalized
        assert "[TOOL] finder_action: skipped: platform is linux, requires darwin" in normalized
        assert "[TOOL] calendar_action: skipped: platform is linux, requires darwin" in normalized
        assert "[TOOL] notes_action: skipped: platform is linux, requires darwin" in normalized
        assert "[TOOL] reminders_action: skipped: platform is linux, requires darwin" in normalized
        assert "[TOOL] messages_action: skipped: platform is linux, requires darwin" in normalized
        assert "[TOOL] fetch_url: registered" in normalized
        assert "[TOOL] read_webpage: registered" in normalized
        assert "[TOOL] extract_page_links: registered" in normalized
        assert "[TOOL] find_capabilities: registered" in normalized
        assert "[TOOL] request_capability: registered" in normalized
    finally:
        runtime.agent.logger.close()
        if runtime.mcp_manager:
            runtime.mcp_manager.close_all()


def test_build_agent_runtime_omits_tool_debug_output_when_disabled(temp_dir, monkeypatch):
    """CLI startup should stay quiet unless tool debug is enabled."""
    from src.main import build_agent_runtime

    monkeypatch.setattr("src.main.LLMClient", StubLLMClient)
    runtime_config = make_runtime_config(temp_dir)
    console = make_recording_console()

    runtime = build_agent_runtime(
        console,
        runtime_config,
        temp_dir,
        skill_debug=False,
        tool_debug=False,
    )
    try:
        normalized = normalize_output(console.export_text())
        assert "[TOOL]" not in normalized
    finally:
        runtime.agent.logger.close()
        if runtime.mcp_manager:
            runtime.mcp_manager.close_all()


def test_main_reads_tool_debug_env(monkeypatch, temp_dir):
    """The main entrypoint should pass TOOL_DEBUG through to CLI runtime assembly."""
    import src.main as main_mod

    monkeypatch.setenv("TOOL_DEBUG", "1")
    console = make_recording_console()
    runtime_config = make_runtime_config(temp_dir)
    seen = {}

    monkeypatch.setattr(main_mod, "build_console", lambda: console)
    monkeypatch.setattr(main_mod, "load_runtime_config", lambda _console: runtime_config)
    monkeypatch.setattr(main_mod, "print_banner", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_mod, "run_repl", lambda *_args, **_kwargs: None)

    def fake_build_agent_runtime(console, runtime_config, cwd, *, skill_debug, tool_debug):
        seen["tool_debug"] = tool_debug
        console.print("[dim][TOOL] Debug mode enabled[/dim]")
        return SimpleNamespace(
            agent=SimpleNamespace(logger=SimpleNamespace(close=lambda: None)),
            mcp_manager=None,
        )

    monkeypatch.setattr(main_mod, "build_agent_runtime", fake_build_agent_runtime)

    main_mod.main()

    normalized = normalize_output(console.export_text())
    assert seen["tool_debug"] is True
    assert "[TOOL] Debug mode enabled" in normalized
