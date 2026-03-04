"""Tests for prompt input helper slash command behavior."""

from contextlib import contextmanager

from prompt_toolkit.application import create_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from src.input_helper import (
    InputHelper,
    _get_active_skill_fragment,
    _get_active_slash_fragment,
)


@contextmanager
def create_input_helper(tmp_path, *, mouse_support=False):
    """Create an InputHelper with deterministic prompt_toolkit IO."""
    with create_pipe_input() as pipe_input:
        output = DummyOutput()
        with create_app_session(input=pipe_input, output=output):
            helper = InputHelper(
                history_file=tmp_path / "history.txt",
                command_names=["help", "tool", "mcp"],
                command_descriptions={
                    "help": "Show all commands",
                    "tool": "List available tools",
                    "mcp": "List MCP servers",
                },
                skill_names=["pdf", "terraform"],
                mouse_support=mouse_support,
                input=pipe_input,
                output=output,
            )
            yield helper, pipe_input, output


def create_buffer(text: str) -> Buffer:
    """Create a buffer with the cursor at the end of the text."""
    return Buffer(document=Document(text, cursor_position=len(text)))


def test_get_active_slash_fragment_cases():
    """Slash fragment detection should track the current token only."""
    assert _get_active_slash_fragment(Document("/", 1)) == ("/", -1)
    assert _get_active_slash_fragment(Document("/he", 3)) == ("/he", -3)
    assert _get_active_slash_fragment(Document("cd /tm", 6)) == ("/tm", -3)
    assert _get_active_slash_fragment(Document("foo/bar", 7)) == ("/bar", -4)
    assert _get_active_slash_fragment(Document("foo / bar", 9)) is None


def test_get_active_skill_fragment_cases():
    """Skill fragment detection should track the current token only."""
    assert _get_active_skill_fragment(Document("$", 1)) == ("$", -1)
    assert _get_active_skill_fragment(Document("$pd", 3)) == ("$pd", -3)
    assert _get_active_skill_fragment(Document("use $pd", 7)) == ("$pd", -3)
    assert _get_active_skill_fragment(Document("$HOME/path", 10)) == ("$HOME/path", -10)
    assert _get_active_skill_fragment(Document("$ pdf", 5)) is None


def test_get_document_completions_match_current_fragment(tmp_path):
    """Completions should be prefix matched against the active slash fragment."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        assert [c.text for c in helper._get_document_completions(Document("/", 1))] == [
            "/help",
            "/tool",
            "/mcp",
        ]
        assert [c.text for c in helper._get_document_completions(Document("/h", 2))] == [
            "/help"
        ]
        assert helper._get_document_completions(Document("/tmp/path", 9)) == []
        assert helper._get_document_completions(Document("cd /tm", 6)) == []
        assert [c.text for c in helper._get_document_completions(Document("$", 1))] == [
            "$pdf",
            "$terraform",
        ]
        assert [c.text for c in helper._get_document_completions(Document("$p", 2))] == [
            "$pdf"
        ]
        assert helper._get_document_completions(Document("$HOME", 5)) == []


def test_input_helper_defaults_mouse_support_off(tmp_path):
    """Mouse support should be disabled by default for terminal scrollback."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        assert helper.session.app.mouse_support() is False


def test_input_helper_can_enable_mouse_support(tmp_path):
    """Mouse support can still be enabled explicitly."""
    with create_input_helper(tmp_path, mouse_support=True) as (helper, _pipe_input, _output):
        assert helper.session.app.mouse_support() is True


def test_handle_slash_opens_menu_and_tab_inserts_selected_command(tmp_path):
    """Slash should open the menu and accepted completion should replace only the fragment."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        buffer = create_buffer("foo")

        helper._handle_slash(buffer)

        assert buffer.text == "foo/"
        assert buffer.complete_state is not None
        assert buffer.complete_state.current_completion.text == "/help"

        helper._move_completion_next(buffer)
        assert buffer.complete_state.current_completion.text == "/tool"

        helper._accept_completion(buffer)
        assert buffer.text == "foo/tool"


def test_handle_dollar_opens_menu_and_tab_inserts_selected_skill(tmp_path):
    """Dollar should open the menu and accepted completion should replace only the fragment."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        buffer = create_buffer("use ")

        helper._handle_dollar(buffer)

        assert buffer.text == "use $"
        assert buffer.complete_state is not None
        assert buffer.complete_state.current_completion.text == "$pdf"

        helper._move_completion_next(buffer)
        assert buffer.complete_state.current_completion.text == "$terraform"

        helper._accept_completion(buffer)
        assert buffer.text == "use $terraform"


def test_completion_replaces_only_active_fragment(tmp_path):
    """Applying a completion should leave the rest of the line untouched."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        buffer = create_buffer("cd /he")
        helper._refresh_completion_menu(buffer, select_first=True)

        helper._accept_completion(buffer)

        assert buffer.text == "cd /help"


def test_typing_path_after_slash_dismisses_menu(tmp_path):
    """Users should be able to keep typing paths without command interference."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        buffer = create_buffer("")
        helper._handle_slash(buffer)

        buffer.insert_text("tmp/path")
        helper._on_buffer_text_changed(buffer)

        assert buffer.text == "/tmp/path"
        assert buffer.complete_state is None


def test_escape_cancels_completion_menu(tmp_path):
    """Escape should dismiss the completion menu."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        buffer = create_buffer("/")
        helper._refresh_completion_menu(buffer, select_first=True)

        helper._cancel_completion(buffer)

        assert buffer.complete_state is None


def test_update_commands_preserves_slash_aware_completer(tmp_path):
    """Updating commands should keep slash-fragment completion behavior."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        helper.update_commands(
            ["deploy"],
            {"deploy": "Deploy the application"},
        )

        completions = helper._get_document_completions(Document("/d", 2))

        assert [c.text for c in completions] == ["/deploy"]
        assert completions[0].display_meta_text == "Deploy the application"


def test_update_skills_preserves_skill_completion(tmp_path):
    """Updating skills should keep $skill completion behavior."""
    with create_input_helper(tmp_path) as (helper, _pipe_input, _output):
        helper.update_skills(["postgres"])

        completions = helper._get_document_completions(Document("$p", 2))

        assert [c.text for c in completions] == ["$postgres"]
        assert completions[0].display_meta_text == "Preload postgres for this turn"


def test_get_input_round_trips_paths(tmp_path):
    """Prompt input should still allow slash-prefixed paths and inline slashes."""
    with create_input_helper(tmp_path) as (helper, pipe_input, _output):
        pipe_input.send_text("/tmp/path\n")
        assert helper.get_input("> ") == "/tmp/path"

        pipe_input.send_text("foo/bar\n")
        assert helper.get_input("> ") == "foo/bar"


def test_get_input_round_trips_unmatched_dollar_tokens(tmp_path):
    """Unknown $tokens should remain normal input text."""
    with create_input_helper(tmp_path) as (helper, pipe_input, _output):
        pipe_input.send_text("$HOME/path\n")
        assert helper.get_input("> ") == "$HOME/path"


def test_bottom_toolbar_callback_is_rendered_and_exposed_as_text(tmp_path):
    """Idle toolbar callbacks should feed the prompt toolbar and plain-text inspection."""
    with create_input_helper(tmp_path) as (_helper, pipe_input, output):
        helper = InputHelper(
            history_file=tmp_path / "history-toolbar.txt",
            command_names=["help"],
            command_descriptions={"help": "Show commands"},
            skill_names=[],
            bottom_toolbar_callback=lambda: HTML("<style fg='ansicyan'>BUILD</style>"),
            input=pipe_input,
            output=output,
        )

        assert helper.get_bottom_toolbar_text() == "BUILD"
        assert helper.build_bottom_toolbar() is not None


def test_toggle_plan_mode_invokes_callback(tmp_path):
    """The prompt shortcut handler should invoke the plan toggle callback."""
    called = {"count": 0}

    with create_pipe_input() as pipe_input:
        output = DummyOutput()
        with create_app_session(input=pipe_input, output=output):
            helper = InputHelper(
                history_file=tmp_path / "history-toggle.txt",
                command_names=[],
                skill_names=[],
                toggle_plan_mode_callback=lambda: called.__setitem__("count", called["count"] + 1),
                input=pipe_input,
                output=output,
            )

            helper.toggle_plan_mode()

    assert called["count"] == 1
