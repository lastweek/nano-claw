"""Skill slash commands."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel

from src.commands.common import (
    get_skill_dependencies,
    print_skill_list,
    render_resource_inventory,
)
from src.commands.registry import (
    CommandHelpSpec,
    CommandSubcommandHelp,
    render_command_help,
    render_unknown_subcommand,
)


def register_skill_commands(registry) -> None:
    """Register `/skill`."""
    skill_help_spec = CommandHelpSpec(
        summary="List, inspect, pin, unpin, or reload skills for the current session.",
        usage=[
            "/skill",
            "/skill use <name>",
            "/skill clear <name|all>",
            "/skill show <name>",
            "/skill reload",
        ],
        examples=["/skill", "/skill help use", "/skill show pdf"],
        notes=[
            "/skill without arguments lists discovered skills.",
            "Pinned skills stay active for future turns in the current session.",
        ],
        subcommands=[
            CommandSubcommandHelp(
                name="use",
                usage="/skill use <name>",
                description="Pin a skill for future turns in the current session.",
                examples=["/skill use pdf"],
            ),
            CommandSubcommandHelp(
                name="clear",
                usage="/skill clear <name|all>",
                description="Unpin one skill or clear all pinned skills.",
                examples=["/skill clear pdf", "/skill clear all"],
            ),
            CommandSubcommandHelp(
                name="show",
                usage="/skill show <name>",
                description="Show metadata, source path, and resource inventory for a skill.",
                examples=["/skill show pdf"],
            ),
            CommandSubcommandHelp(
                name="reload",
                usage="/skill reload",
                description="Rescan skill directories and refresh the discovered skill list.",
                examples=["/skill reload"],
            ),
        ],
    )

    @registry.register(
        "skill",
        "List, inspect, pin, or reload skills",
        args_description="[use|clear|show|reload] [name]",
        short_desc="Manage skills",
        help_spec=skill_help_spec,
    )
    def cmd_skill(console: Console, args: str, context: Any):
        """Manage discovered skills."""
        skill_manager, session_context = get_skill_dependencies(console, context)
        if not skill_manager or not session_context:
            return

        raw_args = args.strip()
        if not raw_args:
            print_skill_list(console, skill_manager, session_context)
            return

        parts = raw_args.split(maxsplit=1)
        subcommand = parts[0].lower()
        remainder = parts[1].strip() if len(parts) > 1 else ""

        if subcommand == "use":
            if not remainder:
                console.print("[yellow]Missing skill name for /skill use[/yellow]")
                command = registry.get_command("skill")
                if command is not None:
                    render_command_help(console, command, "use")
                return

            skill = skill_manager.get_skill(remainder)
            if skill is None:
                console.print(f"[red]Unknown skill: {remainder}[/red]")
                return

            session_context.activate_skill(skill.name)
            console.print(f"[green]Pinned skill:[/green] {skill.name}")
            return

        if subcommand == "clear":
            if not remainder:
                console.print("[yellow]Missing target for /skill clear[/yellow]")
                command = registry.get_command("skill")
                if command is not None:
                    render_command_help(console, command, "clear")
                return

            if remainder.lower() == "all":
                cleared = len(session_context.get_active_skills())
                session_context.clear_skills()
                console.print(f"[green]Cleared {cleared} pinned skill(s)[/green]")
                return

            if remainder not in session_context.get_active_skills():
                console.print(f"[yellow]Skill not pinned: {remainder}[/yellow]")
                return

            session_context.deactivate_skill(remainder)
            console.print(f"[green]Unpinned skill:[/green] {remainder}")
            return

        if subcommand == "show":
            if not remainder:
                console.print("[yellow]Missing skill name for /skill show[/yellow]")
                command = registry.get_command("skill")
                if command is not None:
                    render_command_help(console, command, "show")
                return

            skill = skill_manager.get_skill(remainder)
            if skill is None:
                console.print(f"[red]Unknown skill: {remainder}[/red]")
                return

            line_info = f"{skill.body_line_count}"
            if skill.is_oversized:
                line_info += " (over recommended 500 lines; consider moving detail into references/)"

            body = "\n".join([
                f"[bold]Description:[/bold] {skill.description}",
                f"[bold]Source:[/bold] {skill.source}",
                f"[bold]Catalog Visible:[/bold] {'yes' if skill.catalog_visible else 'no'}",
                f"[bold]Skill File:[/bold] {skill.skill_file}",
                f"[bold]Body Lines:[/bold] {line_info}",
                "",
                "[bold]Scripts:[/bold]",
                render_resource_inventory(skill.scripts),
                "",
                "[bold]References:[/bold]",
                render_resource_inventory(skill.references),
                "",
                "[bold]Assets:[/bold]",
                render_resource_inventory(skill.assets),
            ])

            console.print(Panel(body, title=f"Skill: {skill.name}", border_style="cyan"))
            return

        if subcommand == "reload":
            warnings = skill_manager.discover()
            removed = []
            for skill_name in list(session_context.get_active_skills()):
                if skill_manager.get_skill(skill_name) is None:
                    session_context.deactivate_skill(skill_name)
                    removed.append(skill_name)

            for warning in warnings:
                console.print(f"[yellow]Skill warning: {warning}[/yellow]")

            for skill_name in removed:
                console.print(f"[yellow]Removed missing pinned skill: {skill_name}[/yellow]")

            input_helper = context.get("input_helper")
            if input_helper is not None:
                input_helper.update_skills([skill.name for skill in skill_manager.list_skills()])

            console.print(f"[green]Reloaded {len(skill_manager.list_skills())} skill(s)[/green]")
            return

        command = registry.get_command("skill")
        if command is not None:
            render_unknown_subcommand(console, command, subcommand)
            return

        console.print(f"[red]Unknown /skill subcommand: {subcommand}[/red]")
