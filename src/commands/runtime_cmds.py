"""Runtime reload slash commands."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel

from src.commands.registry import (
    CommandHelpSpec,
    CommandSubcommandHelp,
    render_command_help,
    render_unknown_subcommand,
)


def register_runtime_commands(registry) -> None:
    """Register `/runtime`."""
    help_spec = CommandHelpSpec(
        summary="Reload config-backed tools and skills for the current CLI session.",
        usage=["/runtime reload"],
        examples=["/runtime reload"],
        subcommands=[
            CommandSubcommandHelp(
                name="reload",
                usage="/runtime reload",
                description="Refresh config, skills, extensions, MCP tools, and the active tool registry.",
            )
        ],
    )

    @registry.register(
        "runtime",
        "Reload the current CLI runtime",
        args_description="reload",
        short_desc="Reload runtime",
        help_spec=help_spec,
    )
    def cmd_runtime(console: Console, args: str, context: Any) -> None:
        raw_args = args.strip()
        if not raw_args:
            render_command_help(console, registry.get_command("runtime"))
            return

        if raw_args != "reload":
            command = registry.get_command("runtime")
            if command is not None:
                render_unknown_subcommand(console, command, raw_args.split()[0])
            return

        refresh_callback = context.get("runtime_refresh_callback")
        if refresh_callback is None:
            console.print("[red]Runtime reload is not available in this session[/red]")
            return

        payload = refresh_callback("cli:/runtime reload")
        lines = [
            f"[bold]Tool profile:[/bold] {payload['tool_profile']}",
            f"[bold]Added tools:[/bold] {', '.join(payload['added_tools']) or 'none'}",
            f"[bold]Removed tools:[/bold] {', '.join(payload['removed_tools']) or 'none'}",
            f"[bold]Added skills:[/bold] {', '.join(payload['added_skills']) or 'none'}",
            f"[bold]Removed skills:[/bold] {', '.join(payload['removed_skills']) or 'none'}",
        ]
        pruned = payload.get("pruned_skills") or []
        if pruned:
            lines.append(
                "[bold]Pruned pinned skills:[/bold] "
                + ", ".join(f"{item['name']} ({item['reason']})" for item in pruned)
            )
        resolved_requests = payload.get("resolved_capability_request_ids") or []
        if resolved_requests:
            lines.append(
                "[bold]Resolved capability requests:[/bold] "
                + ", ".join(resolved_requests)
            )
        warnings = payload.get("warnings") or []
        if warnings:
            lines.append("[bold]Warnings:[/bold]")
            lines.extend(f"- {warning}" for warning in warnings)
        console.print(Panel("\n".join(lines), title="Runtime Reload", border_style="cyan"))
