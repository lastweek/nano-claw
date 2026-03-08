"""Extension discovery and install slash commands."""

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


def register_extension_commands(registry) -> None:
    """Register `/extension`."""
    help_spec = CommandHelpSpec(
        summary="Inspect, install, or reload runtime extension bundles.",
        usage=[
            "/extension",
            "/extension show <name>",
            "/extension install <catalog>:<package>",
            "/extension reload",
        ],
        examples=["/extension", "/extension show web-tools", "/extension install curated:github"],
        subcommands=[
            CommandSubcommandHelp(
                name="show",
                usage="/extension show <name>",
                description="Show one discovered extension manifest and exported tools.",
            ),
            CommandSubcommandHelp(
                name="install",
                usage="/extension install <catalog>:<package>",
                description="Install one curated extension into the user-global extension root.",
            ),
            CommandSubcommandHelp(
                name="reload",
                usage="/extension reload",
                description="Reload the current session so new extension tools and skills become active.",
            ),
        ],
    )

    @registry.register(
        "extension",
        "List, inspect, install, or activate extensions",
        args_description="[show|install|reload] [name]",
        short_desc="Manage extensions",
        help_spec=help_spec,
    )
    def cmd_extension(console: Console, args: str, context: Any) -> None:
        manager = context.get("extension_manager")
        if manager is None:
            console.print("[yellow]Extensions are not initialized[/yellow]")
            return

        raw_args = args.strip()
        if not raw_args:
            extensions = manager.list_extensions()
            if not extensions:
                console.print("[yellow]No extensions discovered[/yellow]")
                return

            table = Table(title=f"Extensions ({len(extensions)})", show_header=True, header_style="bold cyan")
            table.add_column("Name", style="green", width=22)
            table.add_column("Version", style="magenta", width=12)
            table.add_column("Scope", style="dim", width=10)
            table.add_column("Tools", style="cyan", width=8)
            table.add_column("Description", style="white")
            for extension in extensions:
                table.add_row(
                    extension.name,
                    extension.version,
                    extension.install_scope,
                    str(len(extension.tool_specs)),
                    extension.description,
                )
            console.print(table)
            return

        parts = raw_args.split(maxsplit=1)
        subcommand = parts[0].lower()
        remainder = parts[1].strip() if len(parts) > 1 else ""

        if subcommand == "show":
            if not remainder:
                command = registry.get_command("extension")
                if command is not None:
                    render_command_help(console, command, "show")
                return
            extension = manager.get_extension(remainder)
            if extension is None:
                console.print(f"[red]Unknown extension: {remainder}[/red]")
                return
            body = "\n".join(
                [
                    f"[bold]Description:[/bold] {extension.description}",
                    f"[bold]Version:[/bold] {extension.version}",
                    f"[bold]Scope:[/bold] {extension.install_scope}",
                    f"[bold]Root:[/bold] {extension.root_dir}",
                    f"[bold]Manifest:[/bold] {extension.manifest_file}",
                    f"[bold]Command:[/bold] {' '.join(extension.command)}",
                    f"[bold]Skill Root:[/bold] {extension.skill_root or 'n/a'}",
                    "",
                    "[bold]Tools:[/bold]",
                    *[
                        f"- {tool_spec.name}: {tool_spec.description}"
                        for tool_spec in extension.tool_specs
                    ],
                ]
            )
            console.print(Panel(body, title=f"Extension: {extension.name}", border_style="cyan"))
            return

        if subcommand == "install":
            if not remainder:
                command = registry.get_command("extension")
                if command is not None:
                    render_command_help(console, command, "install")
                return
            try:
                result = manager.install_from_catalog(remainder)
            except Exception as exc:
                console.print(f"[red]Failed to install extension: {exc}[/red]")
                return
            context["extension_manager"] = manager
            console.print(
                f"[green]Installed extension:[/green] {result.extension.name} {result.extension.version}"
            )
            console.print("[dim]Run /runtime reload to activate new extension tools and skills.[/dim]")
            return

        if subcommand == "reload":
            refresh_callback = context.get("runtime_refresh_callback")
            if refresh_callback is None:
                console.print("[red]Runtime reload is not available in this session[/red]")
                return
            payload = refresh_callback("cli:/extension reload")
            console.print(
                Panel(
                    "\n".join(
                        [
                            f"[bold]Added tools:[/bold] {', '.join(payload['added_tools']) or 'none'}",
                            f"[bold]Added skills:[/bold] {', '.join(payload['added_skills']) or 'none'}",
                            "[bold]Resolved capability requests:[/bold] "
                            f"{', '.join(payload.get('resolved_capability_request_ids') or []) or 'none'}",
                        ]
                    ),
                    title="Extension Reload",
                    border_style="cyan",
                )
            )
            return

        command = registry.get_command("extension")
        if command is not None:
            render_unknown_subcommand(console, command, subcommand)
