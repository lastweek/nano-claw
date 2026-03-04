"""MCP slash commands."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.commands.registry import CommandHelpSpec


def register_mcp_commands(registry) -> None:
    """Register `/mcp`."""
    mcp_help_spec = CommandHelpSpec(
        summary="List configured MCP servers and the tools they expose. Optionally filter to one server.",
        usage=["/mcp", "/mcp <server_name>"],
        examples=["/mcp", "/mcp deepwiki"],
    )

    @registry.register(
        "mcp",
        "List MCP servers and their tools",
        args_description="[server_name]",
        short_desc="List MCP servers",
        help_spec=mcp_help_spec,
    )
    def cmd_mcp(console: Console, args: str, context: Any):
        """List MCP servers and their tools."""
        mcp_manager = context.get("mcp_manager")
        if not mcp_manager:
            console.print("[yellow]No MCP manager initialized[/yellow]")
            return

        agent = context.get("agent")
        if not agent:
            console.print("[red]Agent not available[/red]")
            return

        servers = mcp_manager._servers
        filter_str = args.strip().lower() if args.strip() else None

        if filter_str and filter_str not in servers:
            console.print(f"[yellow]Server '{filter_str}' not found[/yellow]")
            console.print(f"[dim]Available servers: {', '.join(servers.keys())}[/dim]")
            return

        servers_to_show = {filter_str: servers[filter_str]} if filter_str else servers
        for server_name, server in servers_to_show.items():
            server_tools = [
                tool for tool in agent.tools._tools.values()
                if hasattr(tool, "_server") and tool._server.name == server_name
            ]

            panel_title = Text()
            panel_title.append(f"MCP Server: {server_name}", style="bold cyan")
            panel_title.append(f" ({server.url})", style="dim")

            console.print(Panel(
                f"Status: {'[green]Connected[/green]' if server_tools else '[yellow]No tools[/yellow]'}\n"
                f"Tools: {len(server_tools)}\n"
                f"Timeout: {server.timeout}s",
                title=panel_title,
                border_style="cyan",
            ))

            if server_tools:
                tool_table = Table(show_header=True, header_style="dim")
                tool_table.add_column("Tool", style="green", width=25)
                tool_table.add_column("Description", style="white")

                for tool in server_tools:
                    tool_name = tool._tool_def.get("name", "")
                    tool_desc = tool._tool_def.get("description", "")
                    tool_table.add_row(tool_name, tool_desc)

                console.print(tool_table)

            console.print()
