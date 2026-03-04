"""Subagent slash commands."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.commands.common import get_subagent_dependencies
from src.commands.registry import (
    CommandHelpSpec,
    CommandSubcommandHelp,
    render_command_help,
    render_unknown_subcommand,
)


def register_subagent_commands(registry) -> None:
    """Register `/subagent`."""
    subagent_help_spec = CommandHelpSpec(
        summary="Inspect or run local delegated child-agent tasks for the current top-level session.",
        usage=[
            "/subagent",
            "/subagent run <task>",
            "/subagent show <id>",
        ],
        examples=["/subagent", "/subagent run audit the logging flow", "/subagent show sa_0001_abcd1234"],
        notes=[
            "/subagent without arguments lists runs created in the current session.",
            "Slash-command subagent runs do not modify the parent conversation history.",
        ],
        subcommands=[
            CommandSubcommandHelp(
                name="run",
                usage="/subagent run <task>",
                description="Run one delegated child agent synchronously and print its structured result.",
                examples=["/subagent run review the logging flow and summarize problems"],
            ),
            CommandSubcommandHelp(
                name="show",
                usage="/subagent show <id>",
                description="Show the stored metadata and report for one prior subagent run.",
                examples=["/subagent show sa_0001_abcd1234"],
            ),
        ],
    )

    @registry.register(
        "subagent",
        "List or run local subagents",
        args_description="[run <task>|show <id>]",
        short_desc="Manage subagent runs",
        help_spec=subagent_help_spec,
    )
    def cmd_subagent(console: Console, args: str, context: Any):
        """List, run, or inspect local subagent runs."""
        agent, subagent_manager = get_subagent_dependencies(console, context)
        if agent is None or subagent_manager is None:
            return

        if not subagent_manager.enabled:
            console.print("[yellow]Subagents are disabled in the current configuration[/yellow]")
            return

        raw_args = args.strip()
        if not raw_args:
            runs = subagent_manager.list_runs()
            if not runs:
                console.print("[yellow]No subagent runs in this session[/yellow]")
                return

            table = Table(title=f"Subagent Runs ({len(runs)})", show_header=True, header_style="bold cyan")
            table.add_column("ID", style="green", width=18)
            table.add_column("Label", style="white", width=20)
            table.add_column("Status", style="cyan", width=12)
            table.add_column("Duration", style="magenta", width=10)
            table.add_column("Summary", style="white", width=46)

            for run in runs:
                summary = run.result.summary if run.result else ""
                if len(summary) > 80:
                    summary = summary[:77] + "..."
                duration = f"{run.duration_s:.2f}s" if run.duration_s is not None else "-"
                table.add_row(run.subagent_id, run.label, run.status, duration, summary)

            console.print(table)
            return

        parts = raw_args.split(maxsplit=1)
        subcommand = parts[0].lower()
        remainder = parts[1].strip() if len(parts) > 1 else ""
        command = registry.get_command("subagent")

        if subcommand == "run":
            if not remainder:
                console.print("[yellow]Missing task for /subagent run[/yellow]")
                if command is not None:
                    render_command_help(console, command, "run")
                return

            request = subagent_manager.build_subagent_request({"task": remainder})
            result = subagent_manager.run_subagents(
                agent,
                [request],
                parent_turn_id=None,
            )[0]
            metadata = "\n".join([
                f"[bold]ID:[/bold] {result.subagent_id}",
                f"[bold]Label:[/bold] {result.label}",
                f"[bold]Status:[/bold] {result.status}",
                f"[bold]LLM Log:[/bold] {result.llm_log}",
                f"[bold]Events Log:[/bold] {result.events_log}",
            ])
            console.print(Panel(metadata, title="Subagent Result", border_style="cyan"))
            if result.summary:
                console.print(f"[bold]Summary:[/bold] {result.summary}")
            if result.report:
                console.print(result.report)
            if result.error:
                console.print(f"[yellow]{result.error}[/yellow]")
            return

        if subcommand == "show":
            if not remainder:
                console.print("[yellow]Missing subagent id for /subagent show[/yellow]")
                if command is not None:
                    render_command_help(console, command, "show")
                return

            run = subagent_manager.get_run(remainder)
            if run is None:
                console.print(f"[red]Unknown subagent id: {remainder}[/red]")
                return

            result = run.result
            duration_str = f"{run.duration_s:.2f}s" if run.duration_s is not None else "-"
            metadata_lines = [
                f"[bold]ID:[/bold] {run.subagent_id}",
                f"[bold]Label:[/bold] {run.label}",
                f"[bold]Status:[/bold] {run.status}",
                f"[bold]Task:[/bold] {run.task}",
                f"[bold]Started:[/bold] {run.started_at}",
                f"[bold]Ended:[/bold] {run.ended_at or '-'}",
                f"[bold]Duration:[/bold] {duration_str}",
            ]
            if result is not None:
                metadata_lines.extend([
                    f"[bold]Session Dir:[/bold] {result.session_dir}",
                    f"[bold]LLM Log:[/bold] {result.llm_log}",
                    f"[bold]Events Log:[/bold] {result.events_log}",
                    f"[bold]Tools Used:[/bold] {', '.join(result.tools_used) if result.tools_used else 'none'}",
                ])
            console.print(Panel("\n".join(metadata_lines), title="Subagent Run", border_style="cyan"))
            if result:
                if result.summary:
                    console.print(f"[bold]Summary:[/bold] {result.summary}")
                if result.report:
                    console.print(result.report)
                if result.error:
                    console.print(f"[yellow]{result.error}[/yellow]")
            return

        if command is not None:
            render_unknown_subcommand(console, command, subcommand)
            return

        console.print(f"[red]Unknown /subagent subcommand: {subcommand}[/red]")
