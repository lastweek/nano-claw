"""Tests for live turn control toggles."""

from src.turn_activity import TurnActivityEvent
from src.turn_controls import LiveTurnControls
from src.turn_display import TurnProgressDisplay


class NonInteractiveInput:
    """Minimal fake stdin that is not interactive."""

    def isatty(self) -> bool:
        return False


def test_handle_key_toggles_mode_and_hint():
    """The live control keys should mutate display state."""
    display = TurnProgressDisplay(live_activity_mode="simple", live_activity_details="collapsed")
    controls = LiveTurnControls(display, input_stream=NonInteractiveInput())

    assert controls.handle_key("v") is True
    assert display.live_state.mode == "verbose"

    assert controls.handle_key("?") is True
    assert display.live_state.show_controls_hint is True

    assert controls.handle_key("x") is False


def test_start_is_noop_for_non_tty_input():
    """Non-interactive stdin should disable live controls cleanly."""
    display = TurnProgressDisplay()
    controls = LiveTurnControls(display, input_stream=NonInteractiveInput())
    assert controls.start() is False
    controls.stop()


def test_controls_do_not_change_persisted_summary_content():
    """Live key toggles should not mutate the concise persisted summary."""
    display = TurnProgressDisplay(live_activity_mode="simple", live_activity_details="collapsed")
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
    before = display.summary_lines[:]

    controls = LiveTurnControls(display, input_stream=NonInteractiveInput())
    controls.handle_key("v")
    controls.handle_key("?")

    assert display.summary_lines == before
