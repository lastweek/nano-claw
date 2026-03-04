"""Input handling with prompt_toolkit for better UX."""

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, CompleteEvent, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML, to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import has_completions
from prompt_toolkit.keys import Keys
from prompt_toolkit.output.base import Output
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.formatted_text.base import AnyFormattedText


def _get_active_prefixed_fragment(
    document: Document,
    prefix: str,
) -> Optional[Tuple[str, int]]:
    """Get the active token fragment that starts with the given prefix."""
    text = document.text_before_cursor
    if not text:
        return None

    token_start = len(text)
    while token_start > 0 and not text[token_start - 1].isspace():
        token_start -= 1

    token = text[token_start:]
    prefix_index = token.rfind(prefix)
    if prefix_index == -1:
        return None

    fragment = token[prefix_index:]
    if not fragment:
        return None

    return fragment, -len(fragment)


def _get_active_slash_fragment(document: Document) -> Optional[Tuple[str, int]]:
    """Get the active slash fragment before the cursor."""
    return _get_active_prefixed_fragment(document, "/")


def _get_active_skill_fragment(document: Document) -> Optional[Tuple[str, int]]:
    """Get the active $skill fragment before the cursor."""
    return _get_active_prefixed_fragment(document, "$")


class PrefixCommandCompleter(Completer):
    """Prefix-based completer for slash commands and $skills."""

    def __init__(
        self,
        prefix: str,
        names: List[str],
        descriptions: Optional[Dict[str, str]] = None,
    ):
        """Initialize a prefix-aware completer.

        Args:
            prefix: Trigger prefix, such as / or $
            names: List of candidate names without the prefix
            descriptions: Optional descriptions for completion metadata
        """
        self.prefix = prefix
        self.names = list(names)
        self.descriptions = dict(descriptions or {})

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent
    ):
        """Get completions for the active prefixed fragment."""
        active_fragment = _get_active_prefixed_fragment(document, self.prefix)
        if active_fragment is None:
            return []

        fragment, start_position = active_fragment
        query = fragment[1:].lower()

        for name in self.names:
            if query and not name.lower().startswith(query):
                continue

            description = self.descriptions.get(name, "")
            yield Completion(
                text=f"{self.prefix}{name}",
                start_position=start_position,
                display=f"{self.prefix}{name}",
                display_meta=description,
            )


class InputHelper:
    """Enhanced input with bash-like editing and command menu."""

    def __init__(
        self,
        history_file: Optional[Path] = None,
        command_names: Optional[List[str]] = None,
        command_descriptions: Optional[dict] = None,
        skill_names: Optional[List[str]] = None,
        bottom_toolbar_callback: Optional[Callable[[], AnyFormattedText]] = None,
        toggle_plan_mode_callback: Optional[Callable[[], None]] = None,
        mouse_support: bool = False,
        input: Optional[Input] = None,
        output: Optional[Output] = None,
    ):
        """Initialize input session with history and auto-command completion.

        Args:
            history_file: Path to command history file (default: ~/.nano-claw-history)
            command_names: List of command names for tab completion (without /)
            command_descriptions: Dict mapping command names to short descriptions
            skill_names: List of skill names for $skill completion (without $)
            bottom_toolbar_callback: Optional callback for idle statusline content
            toggle_plan_mode_callback: Optional callback for toggling plan mode
            mouse_support: Whether prompt_toolkit mouse tracking is enabled
            input: Optional prompt_toolkit input for testing
            output: Optional prompt_toolkit output for testing
        """
        if history_file is None:
            history_file = Path.home() / ".nano-claw-history"

        self.history = FileHistory(str(history_file))
        self.command_names = list(command_names or [])
        self.command_descriptions = dict(command_descriptions or {})
        self.skill_names = list(skill_names or [])
        self.bottom_toolbar_callback = bottom_toolbar_callback
        self.toggle_plan_mode_callback = toggle_plan_mode_callback

        self.command_completer = self._build_command_completer()
        self.skill_completer = self._build_skill_completer()
        kb = KeyBindings()

        @kb.add("/")
        def _(event):
            """Insert slash and immediately open the command menu."""
            self._handle_slash(event.app.current_buffer)

        @kb.add("$")
        def _(event):
            """Insert dollar sign and immediately open the skill menu."""
            self._handle_dollar(event.app.current_buffer)

        @kb.add("down", filter=has_completions)
        def _(event):
            """Move to the next command completion."""
            self._move_completion_next(event.app.current_buffer)

        @kb.add("up", filter=has_completions)
        def _(event):
            """Move to the previous command completion."""
            self._move_completion_previous(event.app.current_buffer)

        @kb.add("tab", filter=has_completions)
        def _(event):
            """Insert the selected command completion."""
            self._accept_completion(event.app.current_buffer)

        @kb.add("escape", filter=has_completions)
        def _(event):
            """Dismiss the command completion menu."""
            self._cancel_completion(event.app.current_buffer)

        @kb.add(Keys.BackTab)
        def _(event):
            """Toggle plan mode from the prompt via Shift+Tab."""
            self.toggle_plan_mode()
            event.app.invalidate()

        self.session = PromptSession(
            history=self.history,
            enable_history_search=True,
            mouse_support=mouse_support,
            enable_suspend=True,
            completer=self.command_completer,
            key_bindings=kb,
            complete_in_thread=False,
            complete_while_typing=False,
            complete_style=CompleteStyle.COLUMN,
            reserve_space_for_menu=8,
            bottom_toolbar=self.build_bottom_toolbar,
            input=input,
            output=output,
        )
        self.session.default_buffer.on_text_changed += self._on_buffer_text_changed

    def _build_command_completer(self) -> Optional[PrefixCommandCompleter]:
        """Build the current slash command completer."""
        if not self.command_names:
            return None

        return PrefixCommandCompleter(
            prefix="/",
            names=self.command_names,
            descriptions=self.command_descriptions,
        )

    def _build_skill_completer(self) -> Optional[PrefixCommandCompleter]:
        """Build the current $skill completer."""
        if not self.skill_names:
            return None

        return PrefixCommandCompleter(
            prefix="$",
            names=self.skill_names,
            descriptions={
                name: f"Preload {name} for this turn"
                for name in self.skill_names
            },
        )

    def _get_document_completions(self, document: Document) -> List[Completion]:
        """Get completions for the current document."""
        complete_event = CompleteEvent(completion_requested=True)
        if _get_active_slash_fragment(document) is not None and self.command_completer is not None:
            return list(self.command_completer.get_completions(document, complete_event))

        if _get_active_skill_fragment(document) is not None and self.skill_completer is not None:
            return list(self.skill_completer.get_completions(document, complete_event))

        return []

    def _refresh_completion_menu(self, buffer, *, select_first: bool) -> None:
        """Refresh slash command completions for the current buffer."""
        if (
            _get_active_slash_fragment(buffer.document) is None
            and _get_active_skill_fragment(buffer.document) is None
        ):
            buffer.cancel_completion()
            return

        completions = self._get_document_completions(buffer.document)
        if not completions:
            buffer.cancel_completion()
            return

        state = buffer._set_completions(completions)
        if state is None:
            return

        if select_first:
            state.go_to_index(0)

        buffer.complete_state = state

    def _on_buffer_text_changed(self, buffer) -> None:
        """Keep the slash command menu in sync with the current token."""
        self._refresh_completion_menu(
            buffer,
            select_first=buffer.complete_state is not None,
        )

    def _handle_slash(self, buffer) -> None:
        """Insert slash and open command completions for the new fragment."""
        buffer.insert_text("/")
        self._refresh_completion_menu(buffer, select_first=True)

    def _handle_dollar(self, buffer) -> None:
        """Insert dollar sign and open skill completions for the new fragment."""
        buffer.insert_text("$")
        self._refresh_completion_menu(buffer, select_first=True)

    def _move_completion_next(self, buffer) -> None:
        """Select the next completion."""
        if buffer.complete_state is not None:
            buffer.complete_next()

    def _move_completion_previous(self, buffer) -> None:
        """Select the previous completion."""
        if buffer.complete_state is not None:
            buffer.complete_previous()

    def _accept_completion(self, buffer) -> None:
        """Apply the currently selected completion to the buffer."""
        state = buffer.complete_state
        if state is None or not state.completions:
            return

        completion = state.current_completion or state.completions[0]
        buffer.apply_completion(completion)

    def _cancel_completion(self, buffer) -> None:
        """Dismiss the current completion menu."""
        buffer.cancel_completion()

    def update_commands(
        self,
        command_names: List[str],
        command_descriptions: Optional[Dict[str, str]] = None,
    ) -> None:
        """Update command completer with new command list.

        Args:
            command_names: List of command names (without /)
            command_descriptions: Optional descriptions for the new commands
        """
        self.command_names = list(command_names or [])
        if command_descriptions is not None:
            self.command_descriptions = dict(command_descriptions)
        else:
            self.command_descriptions = {
                name: self.command_descriptions.get(name, f"Run {name} command")
                for name in self.command_names
            }

        self.command_completer = self._build_command_completer()
        self.session.completer = self.command_completer
        self._refresh_completion_menu(
            self.session.default_buffer,
            select_first=self.session.default_buffer.complete_state is not None,
        )

    def update_skills(self, skill_names: List[str]) -> None:
        """Update skill completer with new skill names."""
        self.skill_names = list(skill_names or [])
        self.skill_completer = self._build_skill_completer()
        self._refresh_completion_menu(
            self.session.default_buffer,
            select_first=self.session.default_buffer.complete_state is not None,
        )

    def get_bottom_toolbar_text(self) -> str:
        """Return the current idle statusline text."""
        if self.bottom_toolbar_callback is None:
            return ""
        formatted = to_formatted_text(self.bottom_toolbar_callback())
        return "".join(fragment[1] for fragment in formatted).strip()

    def toggle_plan_mode(self) -> None:
        """Toggle the session between build mode and plan mode."""
        if self.toggle_plan_mode_callback is None:
            return
        self.toggle_plan_mode_callback()

    def build_bottom_toolbar(self) -> AnyFormattedText:
        """Build the prompt-toolkit bottom toolbar."""
        if self.bottom_toolbar_callback is None:
            return ""
        toolbar = self.bottom_toolbar_callback()
        if not toolbar:
            return ""
        return toolbar

    def get_input(self, prompt: str = "> ") -> str:
        """Get user input with enhanced editing.

        Args:
            prompt: Prompt string to display

        Returns:
            User input as string
        """
        import sys

        # Use HTML for colored prompt
        formatted_prompt = HTML(f"<style fg='green'>{prompt}</style>")

        try:
            result = self.session.prompt(
                formatted_prompt,
                # Enable common key bindings
                enable_open_in_editor=True,  # Ctrl+X Ctrl+E to edit in editor
            )
            # CRITICAL: Print newline to clear the prompt line for subsequent output
            # This ensures Rich's console output starts on a fresh line
            sys.stdout.write("\n")
            sys.stdout.flush()
            return result
        except (EOFError, KeyboardInterrupt):
            # User wants to exit
            raise EOFError
