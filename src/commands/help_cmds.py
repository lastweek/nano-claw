"""Help and tool-list slash commands."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from src.commands.registry import CommandHelpSpec


def register_help_commands(registry) -> None:
    """Register `/help` and `/tool`."""
    help_help_spec = CommandHelpSpec(
        summary="List all available slash commands. Slash commands execute locally and do not go through the agent.",
        usage=["/help"],
        examples=["/help"],
    )

    tool_help_spec = CommandHelpSpec(
        summary="List the tools available in the current session. An optional filter matches tool names and descriptions.",
        usage=["/tool", "/tool <filter>"],
        examples=["/tool", "/tool read", "/tool deepwiki"],
    )

    @registry.register(
        "help",
        "Show available commands",
        args_description="",
        short_desc="Show all commands",
        help_spec=help_help_spec,
    )
    def cmd_help(console: Console, args: str, context: Any):
        """Show help for available commands."""
        commands = registry.list_commands()

        table = Table(title="Available Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="green", width=20)
        table.add_column("Description", style="white", width=40)
        table.add_column("Arguments", style="dim", width=30)

        for cmd in commands:
            args_text = cmd.args_description or ""
            table.add_row(f"/{cmd.name}", cmd.description, args_text)

        console.print(table)
        console.print("\n[dim]Type /<command> to execute. Commands don't go through the agent.[/dim]")

    @registry.register(
        "tool",
        "List all available tools",
        args_description="[filter]",
        short_desc="List available tools",
        help_spec=tool_help_spec,
    )
    def cmd_tool(console: Console, args: str, context: Any):
        """List available tools in the current context."""
        agent = context.get("agent")
        if not agent:
            console.print("[red]Agent not available[/red]")
            return

        tools = agent.tools._tools
        filter_str = args.strip().lower() if args.strip() else None
        if filter_str:
            tools = {
                name: tool for name, tool in tools.items()
                if filter_str in name.lower() or filter_str in tool.description.lower()
            }

        table = Table(
            title=f"Available Tools ({len(tools)})",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Tool Name", style="green", width=30)
        table.add_column("Description", style="white", width=50)
        table.add_column("Parameters", style="dim", width=30)

        for name, tool in sorted(tools.items()):
            if tool.parameters:
                if isinstance(tool.parameters, dict):
                    properties = tool.parameters.get("properties", {})
                    param_names = list(properties.keys()) if properties else []
                else:
                    param_names = list(tool.parameters.keys()) if tool.parameters else []
                params_str = ", ".join(param_names) if param_names else "none"
            else:
                params_str = "none"

            table.add_row(name, tool.description, params_str)

        console.print(table)
