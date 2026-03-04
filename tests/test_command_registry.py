"""Tests for slash command registry execution rules."""

import io

from rich.console import Console

from src.commands import builtin
from src.commands.registry import CommandRegistry, CommandHelpSpec, CommandSubcommandHelp


def create_console(buffer: io.StringIO) -> Console:
    """Create a deterministic Rich console for tests."""
    return Console(file=buffer, force_terminal=False, color_system=None)


def test_execute_runs_command_with_leading_whitespace():
    """Commands should execute when slash is the first non-space character."""
    registry = CommandRegistry()
    calls = []

    @registry.register("help", "Show help")
    def cmd_help(console, args, context):
        calls.append((args, context))

    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("   /help topic", console, {"mode": "test"})

    assert executed is True
    assert calls == [("topic", {"mode": "test"})]


def test_execute_ignores_inline_slash_text():
    """Slash text later in the line should be treated as normal user input."""
    registry = CommandRegistry()
    calls = []

    @registry.register("help", "Show help")
    def cmd_help(console, args, context):
        calls.append(args)

    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("show me /help", console, {})

    assert executed is False
    assert calls == []


def test_execute_unknown_command_with_leading_whitespace():
    """Unknown slash commands should still show the existing error text."""
    registry = CommandRegistry()
    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("   /unknown", console, {})

    assert executed is True
    text = output.getvalue()
    assert "Unknown command: /unknown" in text
    assert "Type /help for available commands" in text


def test_execute_command_help_does_not_run_handler():
    """`/command help` should render help without executing the command body."""
    registry = CommandRegistry()
    calls = []

    @registry.register(
        "context",
        "Show context usage",
        help_spec=CommandHelpSpec(summary="Show context usage", usage=["/context"]),
    )
    def cmd_context(console, args, context):
        calls.append(args)

    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("/context help", console, {})

    assert executed is True
    assert calls == []
    text = output.getvalue()
    assert "Command: /context" in text
    assert "Show context usage" in text


def test_execute_command_help_aliases_render_help():
    """`--help` and `-h` should trigger the same command manual path."""
    registry = CommandRegistry()

    @registry.register(
        "context",
        "Show context usage",
        help_spec=CommandHelpSpec(summary="Show context usage", usage=["/context"]),
    )
    def cmd_context(console, args, context):
        raise AssertionError("command handler should not run for help")

    for command_line in ("/context --help", "/context -h"):
        output = io.StringIO()
        console = create_console(output)

        executed = registry.execute(command_line, console, {})

        assert executed is True
        assert "Command: /context" in output.getvalue()


def test_execute_targeted_subcommand_help_renders_without_running_handler():
    """`/command help subcommand` should render targeted subcommand help."""
    registry = CommandRegistry()

    @registry.register(
        "skill",
        "Manage skills",
        help_spec=CommandHelpSpec(
            summary="Manage skills",
            usage=["/skill"],
            subcommands=[
                CommandSubcommandHelp(
                    name="show",
                    usage="/skill show <name>",
                    description="Show a skill",
                )
            ],
        ),
    )
    def cmd_skill(console, args, context):
        raise AssertionError("command handler should not run for targeted help")

    output = io.StringIO()
    console = create_console(output)
    executed = registry.execute("/skill help show", console, {})

    assert executed is True
    text = output.getvalue()
    assert "Command: /skill (show)" in text
    assert "/skill show <name>" in text


def test_builtin_subagent_help_renders_manual():
    """Built-in /subagent help should render the command manual."""
    registry = CommandRegistry()
    builtin.register_all(registry)
    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("/subagent help", console, {})

    assert executed is True
    text = output.getvalue()
    assert "Command: /subagent" in text
    assert "/subagent run <task>" in text


def test_builtin_plan_help_renders_manual():
    """Built-in /plan help should render the command manual."""
    registry = CommandRegistry()
    builtin.register_all(registry)
    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("/plan help", console, {})

    assert executed is True
    text = output.getvalue()
    assert "Command: /plan" in text
    assert "/plan start <task>" in text
