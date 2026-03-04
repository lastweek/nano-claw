"""Tests for shared slash-command help rendering."""

import io

from rich.console import Console

from src.commands.registry import (
    Command,
    CommandHelpSpec,
    CommandSubcommandHelp,
    render_command_help,
)


def create_console(buffer: io.StringIO) -> Console:
    """Create a deterministic Rich console."""
    return Console(file=buffer, force_terminal=False, color_system=None, width=120)


def noop_handler(console, args, context):
    """No-op command handler used in help tests."""


def test_render_command_help_renders_usage_examples_and_subcommands():
    """Command-level help should include the expected manual sections."""
    command = Command(
        name="compact",
        description="Manage context compaction",
        handler=noop_handler,
        help_spec=CommandHelpSpec(
            summary="Inspect or manage context compaction.",
            usage=["/compact", "/compact now"],
            examples=["/compact", "/compact help now"],
            notes=["Compaction is session-local."],
            subcommands=[
                CommandSubcommandHelp(
                    name="now",
                    usage="/compact now",
                    description="Compact immediately.",
                )
            ],
        ),
    )
    output = io.StringIO()
    console = create_console(output)

    render_command_help(console, command)

    text = output.getvalue()
    assert "Command: /compact" in text
    assert "Usage" in text
    assert "Subcommands" in text
    assert "/compact now" in text
    assert "Examples" in text
    assert "Notes" in text


def test_render_command_help_targeted_subcommand_renders_selected_details():
    """Targeted subcommand help should render the selected subcommand information."""
    command = Command(
        name="skill",
        description="Manage skills",
        handler=noop_handler,
        help_spec=CommandHelpSpec(
            summary="Manage session skills.",
            usage=["/skill"],
            subcommands=[
                CommandSubcommandHelp(
                    name="use",
                    usage="/skill use <name>",
                    description="Pin a skill.",
                    examples=["/skill use pdf"],
                ),
                CommandSubcommandHelp(
                    name="show",
                    usage="/skill show <name>",
                    description="Show a skill.",
                ),
            ],
        ),
    )
    output = io.StringIO()
    console = create_console(output)

    render_command_help(console, command, "use")

    text = output.getvalue()
    assert "Command: /skill (use)" in text
    assert "/skill use <name>" in text
    assert "Pin a skill." in text
    assert "/skill show <name>" not in text


def test_render_command_help_falls_back_without_help_spec():
    """Commands without explicit help specs should still get a minimal manual."""
    command = Command(
        name="context",
        description="Show context usage",
        handler=noop_handler,
        args_description="[filter]",
    )
    output = io.StringIO()
    console = create_console(output)

    render_command_help(console, command)

    text = output.getvalue()
    assert "Command: /context" in text
    assert "Show context usage" in text
    assert "/context [filter]" in text
