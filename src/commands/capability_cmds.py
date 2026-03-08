"""Slash commands for missing-capability request inspection."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.commands.registry import (
    CommandHelpSpec,
    CommandSubcommandHelp,
    render_command_help,
    render_unknown_subcommand,
)


def register_capability_commands(registry) -> None:
    """Register `/capability`."""
    help_spec = CommandHelpSpec(
        summary="Inspect or manage missing-capability requests recorded for this session.",
        usage=[
            "/capability",
            "/capability show <request_id>",
            "/capability dismiss <request_id>",
            "/capability resolve <request_id>",
        ],
        examples=["/capability", "/capability show capreq_123", "/capability dismiss capreq_123"],
        subcommands=[
            CommandSubcommandHelp(
                name="show",
                usage="/capability show <request_id>",
                description="Show one capability request in detail.",
            ),
            CommandSubcommandHelp(
                name="dismiss",
                usage="/capability dismiss <request_id>",
                description="Dismiss a pending capability request.",
            ),
            CommandSubcommandHelp(
                name="resolve",
                usage="/capability resolve <request_id>",
                description="Mark a capability request resolved manually.",
            ),
        ],
    )

    @registry.register(
        "capability",
        "Inspect missing-capability requests",
        args_description="[show|dismiss|resolve] [request_id]",
        short_desc="Manage capability requests",
        help_spec=help_spec,
    )
    def cmd_capability(console: Console, args: str, context: Any) -> None:
        manager = context.get("capability_request_manager")
        if manager is None:
            console.print("[yellow]Capability requests are not initialized[/yellow]")
            return

        raw_args = args.strip()
        if not raw_args:
            requests = manager.list_requests()
            if not requests:
                console.print("[yellow]No capability requests recorded[/yellow]")
                return

            table = Table(
                title=f"Capability Requests ({len(requests)})",
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("ID", style="green", width=18)
            table.add_column("Status", style="magenta", width=10)
            table.add_column("Type", style="cyan", width=18)
            table.add_column("Desired Capability", style="white", width=26)
            table.add_column("Occurrences", style="dim", width=10)
            for request in requests:
                table.add_row(
                    request.request_id,
                    request.status,
                    request.request_type,
                    request.desired_capability,
                    str(request.occurrence_count),
                )
            console.print(table)
            return

        parts = raw_args.split(maxsplit=1)
        subcommand = parts[0].lower()
        request_id = parts[1].strip() if len(parts) > 1 else ""
        if subcommand not in {"show", "dismiss", "resolve"}:
            command = registry.get_command("capability")
            if command is not None:
                render_unknown_subcommand(console, command, subcommand)
            return
        if not request_id:
            command = registry.get_command("capability")
            if command is not None:
                render_command_help(console, command, subcommand)
            return

        try:
            if subcommand == "show":
                request = manager.get_request(request_id)
                if request is None:
                    raise KeyError(request_id)
            elif subcommand == "dismiss":
                request = manager.dismiss_request(request_id)
            else:
                request = manager.resolve_request(request_id)
        except KeyError:
            console.print(f"[red]Unknown capability request: {request_id}[/red]")
            return

        body = "\n".join(
            [
                f"[bold]Status:[/bold] {request.status}",
                f"[bold]Type:[/bold] {request.request_type}",
                f"[bold]Summary:[/bold] {request.summary}",
                f"[bold]Reason:[/bold] {request.reason}",
                f"[bold]Desired capability:[/bold] {request.desired_capability}",
                f"[bold]Package:[/bold] {request.package_ref or 'n/a'}",
                f"[bold]Extension:[/bold] {request.extension_name or 'n/a'}",
                f"[bold]Skill:[/bold] {request.skill_name or 'n/a'}",
                f"[bold]Tool:[/bold] {request.tool_name or 'n/a'}",
                f"[bold]Occurrences:[/bold] {request.occurrence_count}",
                f"[bold]Suggested actions:[/bold] {', '.join(request.suggested_cli_actions) or 'none'}",
            ]
        )
        console.print(Panel(body, title=f"Capability Request: {request.request_id}", border_style="cyan"))
