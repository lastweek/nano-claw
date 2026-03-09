"""Slash command system for babyclaw meta-commands."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass, field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


@dataclass(frozen=True)
class CommandSubcommandHelp:
    """Help metadata for a single slash-command subcommand."""

    name: str
    usage: str
    description: str
    examples: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommandHelpSpec:
    """Help metadata for a slash command."""

    summary: str
    usage: List[str]
    examples: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    subcommands: List[CommandSubcommandHelp] = field(default_factory=list)


@dataclass
class Command:
    """Metadata for a slash command."""

    name: str
    description: str
    handler: Callable[..., None]
    args_description: Optional[str] = None
    short_desc: str = ""  # Short description for menu (50 chars max)
    help_spec: Optional[CommandHelpSpec] = None


class CommandRegistry:
    """Registry for slash commands."""

    def __init__(self):
        self._commands: Dict[str, Command] = {}

    def register(
        self,
        name: str,
        description: str,
        args_description: str = None,
        short_desc: str = "",
        help_spec: Optional[CommandHelpSpec] = None,
    ):
        """Decorator to register a command.

        Args:
            name: Command name (without the /)
            description: What the command does
            args_description: Optional description of arguments
            short_desc: Short description for menu (50 chars max)
            help_spec: Optional help/manual metadata for the command

        Example:
            @registry.register("echo", "Print a message", short_desc="Echo text")
            def cmd_echo(console: Console, args: str, context: Any):
                console.print(args)
        """
        def decorator(func: Callable[..., None]):
            self._commands[name] = Command(
                name=name,
                description=description,
                handler=func,
                args_description=args_description,
                short_desc=short_desc,
                help_spec=help_spec,
            )
            return func
        return decorator

    def execute(self, command_line: str, console: Console, context: Any) -> bool:
        """Execute a command line.

        Args:
            command_line: Full command line (e.g., "/tool" or "/mcp deepwiki")
            console: Rich console for output
            context: Application context (agent, mcp_manager, etc.)

        Returns:
            True if command was executed, False if not a command
        """
        stripped_command = command_line.lstrip()
        if not stripped_command.startswith("/"):
            return False

        parts = stripped_command[1:].split(maxsplit=1)
        command_name = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        command = self._commands.get(command_name)
        if not command:
            console.print(f"[red]Unknown command: /{command_name}[/red]")
            console.print(f"[dim]Type /help for available commands[/dim]")
            return True  # It was a command, just unknown

        help_target = self._parse_help_target(args)
        if help_target is not False:
            if help_target is None:
                render_command_help(console, command)
            elif command.help_spec and command.help_spec.subcommands:
                matches = _find_subcommand_help_entries(command, help_target)
                if matches:
                    render_command_help(console, command, help_target)
                else:
                    render_unknown_subcommand(console, command, help_target)
            else:
                render_unknown_subcommand(console, command, help_target)
            return True

        try:
            command.handler(console, args, context)
        except Exception as e:
            console.print(f"[red]Error executing /{command_name}: {e}[/red]")

        return True

    def list_commands(self) -> List[Command]:
        """Get all registered commands."""
        return list(self._commands.values())

    def get_command_names(self) -> List[str]:
        """Get all command names."""
        return list(self._commands.keys())

    def get_command(self, name: str) -> Optional[Command]:
        """Get a registered command by name."""
        return self._commands.get(name)

    @staticmethod
    def _parse_help_target(args: str) -> str | None | bool:
        """Parse slash-command help triggers from the raw argument string.

        Returns:
            None for command-level help,
            a string for targeted subcommand help,
            False when no help trigger is present.
        """
        stripped = args.strip()
        if stripped in {"help", "--help", "-h"}:
            return None
        if stripped.startswith("help "):
            target = stripped[5:].strip()
            return target or None
        return False


def _build_fallback_help_spec(command: Command) -> CommandHelpSpec:
    """Build a minimal help spec if a command forgot to provide one."""
    usage = [f"/{command.name}" + (f" {command.args_description}" if command.args_description else "")]
    return CommandHelpSpec(summary=command.description, usage=usage)


def _get_help_spec(command: Command) -> CommandHelpSpec:
    """Return the configured help spec or a minimal fallback."""
    return command.help_spec or _build_fallback_help_spec(command)


def _find_subcommand_help_entries(command: Command, subcommand: str) -> List[CommandSubcommandHelp]:
    """Find exact or grouped subcommand help entries."""
    spec = _get_help_spec(command)
    target = subcommand.strip().lower()
    if not target:
        return []

    exact_matches = [entry for entry in spec.subcommands if entry.name.lower() == target]
    if exact_matches:
        return exact_matches

    return [entry for entry in spec.subcommands if entry.name.lower().startswith(f"{target} ")]


def _print_usage(console: Console, usage_lines: List[str]) -> None:
    """Render a usage block."""
    console.print("[bold]Usage[/bold]")
    for line in usage_lines:
        text = Text("  ")
        text.append(line, style="cyan")
        console.print(text)


def _print_examples(console: Console, examples: List[str]) -> None:
    """Render an examples block."""
    if not examples:
        return

    console.print()
    console.print("[bold]Examples[/bold]")
    for example in examples:
        text = Text("  ")
        text.append(example, style="cyan")
        console.print(text)


def _print_notes(console: Console, notes: List[str]) -> None:
    """Render a notes block."""
    if not notes:
        return

    console.print()
    console.print("[bold]Notes[/bold]")
    for note in notes:
        console.print(f"  [dim]{note}[/dim]")


def render_command_help(console: Console, command: Command, subcommand: str | None = None) -> None:
    """Render command-level or targeted subcommand help."""
    spec = _get_help_spec(command)

    if subcommand:
        entries = _find_subcommand_help_entries(command, subcommand)
        if not entries:
            render_unknown_subcommand(console, command, subcommand)
            return

        title_suffix = f" ({subcommand})"
        console.print(Panel(spec.summary, title=f"Command: /{command.name}{title_suffix}", border_style="cyan"))
        for index, entry in enumerate(entries):
            if index:
                console.print()
            console.print(f"[bold]{entry.name}[/bold]")
            _print_usage(console, [entry.usage])
            console.print()
            console.print(entry.description)
            _print_examples(console, entry.examples)
            _print_notes(console, entry.notes)
        return

    console.print(Panel(spec.summary, title=f"Command: /{command.name}", border_style="cyan"))
    _print_usage(console, spec.usage)

    if spec.subcommands:
        console.print()
        table = Table(title="Subcommands", show_header=True, header_style="bold cyan")
        table.add_column("Subcommand", style="green", width=16)
        table.add_column("Usage", style="cyan", width=30)
        table.add_column("Description", style="white")
        for entry in spec.subcommands:
            table.add_row(entry.name, entry.usage, entry.description)
        console.print(table)

    _print_examples(console, spec.examples)
    _print_notes(console, spec.notes)


def render_unknown_subcommand(console: Console, command: Command, subcommand: str) -> None:
    """Render a standard unknown-subcommand error followed by the full manual."""
    console.print(f"[red]Unknown /{command.name} subcommand: {subcommand}[/red]")
    render_command_help(console, command)
