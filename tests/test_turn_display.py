"""Tests for the grouped live turn transcript display."""

from rich.console import Console

from src.context import Context
from src.turn_activity import TurnActivityEvent
from src.turn_display import TurnProgressDisplay


def render_text(renderable) -> str:
    """Render a Rich object to plain text for assertions."""
    console = Console(record=True, force_terminal=False, width=120)
    console.print(renderable)
    return console.export_text()


def test_worker_sections_are_ordered_by_first_appearance():
    """Main agent should stay first and subagent sections should follow first-seen order."""
    display = TurnProgressDisplay(live_activity_mode="simple")
    display.handle_event(TurnActivityEvent("subagent_started", details={
        "subagent_id": "sa_1",
        "label": "docs-review",
        "task": "review docs",
    }))
    display.handle_event(TurnActivityEvent(
        "llm_call_finished",
        iteration=0,
        worker_id="sa_1",
        worker_label="docs-review",
        worker_kind="subagent",
        parent_worker_id="main",
        details={
            "stream": False,
            "duration_s": 0.4,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cached_tokens": 0,
            "has_tool_calls": False,
            "tool_call_count": 0,
            "result_kind": "final_answer",
            "assistant_preview": "Reviewed docs.",
            "assistant_body": "Reviewed docs.\nNeed one follow-up.",
            "requested_tool_signatures": [],
        },
    ))

    rendered = render_text(display.render_live())
    assert rendered.index("Main Agent") < rendered.index("Subagent: docs-review")
    assert rendered.count("Main Agent") == 1
    assert rendered.count("Subagent: docs-review") == 1


def test_active_entries_update_in_place_and_finalize():
    """One running entry should become a completed entry after the finish event."""
    display = TurnProgressDisplay(live_activity_mode="simple")
    display.handle_event(TurnActivityEvent("llm_call_started", iteration=0, details={
        "stream": False,
        "message_count": 2,
        "tool_schema_count": 0,
    }))
    running = render_text(display.render_live())
    assert "LLM call 1 running" in running

    display.handle_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
        "stream": False,
        "duration_s": 0.2,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
        "has_tool_calls": False,
        "tool_call_count": 0,
        "result_kind": "final_answer",
        "assistant_preview": "Done.",
        "assistant_body": "Done.",
        "requested_tool_signatures": [],
    }))
    finished = render_text(display.render_live())
    assert "LLM call 1 running" not in finished
    assert "LLM call 1 produced final answer" in finished


def test_simple_mode_renders_headers_only():
    """Simple mode should omit folded body placeholder lines."""
    display = TurnProgressDisplay(live_activity_mode="simple")
    display.handle_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
        "stream": False,
        "duration_s": 0.2,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
        "has_tool_calls": False,
        "tool_call_count": 0,
        "result_kind": "final_answer",
        "assistant_preview": "Need to inspect src/agent.py first.",
        "assistant_body": "Need to inspect src/agent.py first.",
        "requested_tool_signatures": [],
    }))

    rendered = render_text(display.render_live())
    assert "LLM call 1 produced final answer" in rendered
    assert "assistant response: folded" not in rendered


def test_verbose_mode_collapses_bodies_by_default():
    """Verbose mode should render folded placeholders instead of bodies by default."""
    display = TurnProgressDisplay(
        live_activity_mode="verbose",
        live_activity_details="collapsed",
    )
    display.handle_event(TurnActivityEvent("tool_call_finished", iteration=0, details={
        "tool_name": "read_file",
        "tool_call_id": "call_1",
        "arguments": {"file_path": "src/agent.py"},
        "success": True,
        "duration_s": 0.01,
        "result_preview": "Agent loop implementation",
        "result_body": "1    \"\"\"Agent loop implementation.\"\"\"\n2    more",
    }))

    rendered = render_text(display.render_live())
    assert "Tool read_file(file_path='src/agent.py') finished (0.01s)" in rendered
    assert "result: folded" in rendered
    assert "Agent loop implementation" not in rendered


def test_verbose_mode_expanded_shows_sanitized_bodies():
    """Expanded verbose mode should show short assistant and tool body previews."""
    display = TurnProgressDisplay(
        live_activity_mode="verbose",
        live_activity_details="expanded",
    )
    display.handle_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
        "stream": False,
        "duration_s": 0.2,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
        "has_tool_calls": False,
        "tool_call_count": 0,
        "result_kind": "final_answer",
        "assistant_preview": "Need to inspect src/main.py first.",
        "assistant_body": "Need to inspect src/main.py first.\nThen I can continue.",
        "requested_tool_signatures": [],
    }))

    rendered = render_text(display.render_live())
    assert "assistant response:" in rendered
    assert "Need to inspect src/main.py first." in rendered
    assert "Then I can continue." in rendered


def test_run_subagent_is_rendered_as_milestone_not_generic_tool():
    """Subagent dispatch should show as a main-agent milestone instead of a generic tool block."""
    display = TurnProgressDisplay(live_activity_mode="simple")
    display.handle_event(TurnActivityEvent("subagent_started", details={
        "subagent_id": "sa_2",
        "label": "api-audit",
        "task": "audit API flow",
    }))

    rendered = render_text(display.render_live())
    assert "Spawned subagent: api-audit" in rendered
    assert "Tool run_subagent" not in rendered


def test_persisted_summary_remains_concise():
    """The persisted summary should stay flat and concise rather than rendering the grouped transcript."""
    display = TurnProgressDisplay(live_activity_mode="verbose")
    display.handle_event(TurnActivityEvent("llm_call_finished", iteration=0, details={
        "stream": False,
        "duration_s": 0.2,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_tokens": 0,
        "has_tool_calls": True,
        "tool_call_count": 1,
        "result_kind": "tool_calls",
        "assistant_preview": "",
        "assistant_body": "- read_file(file_path='src/agent.py')",
        "requested_tool_signatures": ["read_file(file_path='src/agent.py')"],
    }))

    rendered = render_text(display.render_persisted())
    assert "LLM call 1 requested 1 tool" in rendered
    assert "Main Agent" not in rendered
    assert "assistant response" not in rendered


def test_render_live_includes_statusline_footer():
    """Live rendering should include the shared statusline footer when session context is available."""
    context = Context.create()
    display = TurnProgressDisplay(
        session_context=context,
        live_activity_mode="verbose",
        live_activity_details="collapsed",
    )

    rendered = render_text(display.render_live())

    assert "BUILD | view:verbose | plan:none | tip:Shift+Tab plan mode" in rendered
