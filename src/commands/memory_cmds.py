"""Session memory slash commands."""

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


def register_memory_commands(registry) -> None:
    """Register `/memory`."""
    help_spec = CommandHelpSpec(
        summary="Inspect and update the current session's managed Markdown memory workspace.",
        usage=[
            "/memory",
            "/memory show",
            "/memory remember <kind> <title> :: <content>",
            "/memory daily <title> :: <content>",
            "/memory search <query>",
            "/memory forget <kind> <title>",
            "/memory mode <off|manual_only|auto>",
        ],
        examples=[
            "/memory show",
            "/memory remember decision deploy-order :: Run migration before deploy",
            "/memory daily today-note :: Investigated SSE reconnect issue",
        ],
        subcommands=[
            CommandSubcommandHelp(
                name="show",
                usage="/memory show",
                description="Show the current session MEMORY.md file.",
            ),
            CommandSubcommandHelp(
                name="remember",
                usage="/memory remember <kind> <title> :: <content>",
                description="Upsert one curated memory entry.",
            ),
            CommandSubcommandHelp(
                name="daily",
                usage="/memory daily <title> :: <content>",
                description="Append one timestamped daily memory note.",
            ),
            CommandSubcommandHelp(
                name="search",
                usage="/memory search <query>",
                description="Search curated memory and daily logs.",
            ),
            CommandSubcommandHelp(
                name="forget",
                usage="/memory forget <kind> <title>",
                description="Delete one curated memory entry.",
            ),
            CommandSubcommandHelp(
                name="mode",
                usage="/memory mode <off|manual_only|auto>",
                description="Set the current session memory writeback mode.",
            ),
        ],
    )

    @registry.register(
        "memory",
        "Inspect or update session memory",
        args_description="[show|remember|daily|search|forget] ...",
        short_desc="Manage session memory",
        help_spec=help_spec,
    )
    def cmd_memory(console: Console, args: str, context: Any):
        memory_store = context.get("memory_store")
        session_context = context.get("session_context")
        if memory_store is None or session_context is None:
            console.print("[red]Session memory is not available in this runtime[/red]")
            return
        if not memory_store.is_enabled():
            console.print("[yellow]Session memory is disabled in config[/yellow]")
            return

        raw_args = args.strip()
        if not raw_args:
            summary = memory_store.describe_workspace(session_context.session_id)
            console.print(
                Panel(
                    "\n".join(
                        [
                            f"[bold]Root:[/bold] {summary['root_dir']}",
                            f"[bold]Document:[/bold] {summary['document_path']}",
                            f"[bold]Mode:[/bold] {summary['settings']['mode']}",
                            f"[bold]Entries:[/bold] {summary['entry_count']}",
                            f"[bold]Daily logs:[/bold] {len(summary['daily_files'])}",
                        ]
                    ),
                    title="Session Memory",
                    border_style="cyan",
                )
            )
            return

        parts = raw_args.split(maxsplit=1)
        subcommand = parts[0].lower()
        remainder = parts[1].strip() if len(parts) > 1 else ""

        if subcommand == "show":
            document = memory_store.read_curated_document(session_context.session_id)
            console.print(Panel(document.rstrip(), title="MEMORY.md", border_style="cyan"))
            return

        if subcommand == "search":
            if not remainder:
                console.print("[yellow]Missing query for /memory search[/yellow]")
                command = registry.get_command("memory")
                if command is not None:
                    render_command_help(console, command, "search")
                return
            hits = memory_store.search(
                session_context.session_id,
                query=remainder,
                actor="cli",
            )
            if not hits:
                console.print(f"[yellow]No memory hits for:[/yellow] {remainder}")
                return
            body = "\n\n".join(
                [
                    "\n".join(
                        [
                            f"[bold]{hit.title}[/bold]",
                            f"[dim]{hit.path}[/dim]",
                            f"Entry ID: {hit.entry_id or '-'}",
                            f"Kind: {hit.kind or '-'}",
                            f"Status: {hit.status or '-'}",
                            hit.snippet,
                        ]
                    )
                    for hit in hits
                ]
            )
            console.print(Panel(body, title=f"Memory search: {remainder}", border_style="cyan"))
            return

        if subcommand == "remember":
            parsed = _parse_memory_body(remainder)
            if parsed is None:
                console.print("[yellow]Use /memory remember <kind> <title> :: <content>[/yellow]")
                command = registry.get_command("memory")
                if command is not None:
                    render_command_help(console, command, "remember")
                return
            kind, title, content = parsed
            entry = memory_store.upsert_curated_entry(
                session_context.session_id,
                kind=kind,
                title=title,
                content=content,
                reason="cli /memory remember",
                source="cli",
            )
            console.print(f"[green]Updated memory:[/green] {entry.entry_id}")
            return

        if subcommand == "daily":
            parsed_daily = _parse_daily_body(remainder)
            if parsed_daily is None:
                console.print("[yellow]Use /memory daily <title> :: <content>[/yellow]")
                command = registry.get_command("memory")
                if command is not None:
                    render_command_help(console, command, "daily")
                return
            title, content = parsed_daily
            path = memory_store.append_daily_log(
                session_context.session_id,
                title=title,
                content=content,
                reason="cli /memory daily",
                source="cli",
            )
            console.print(f"[green]Appended daily memory:[/green] {path}")
            return

        if subcommand == "forget":
            forget_parts = remainder.split(maxsplit=1)
            if len(forget_parts) != 2:
                console.print("[yellow]Use /memory forget <kind> <title>[/yellow]")
                command = registry.get_command("memory")
                if command is not None:
                    render_command_help(console, command, "forget")
                return
            kind, title = forget_parts
            path = memory_store.delete_curated_entry(
                session_context.session_id,
                kind=kind,
                title=title,
                reason="cli /memory forget",
            )
            console.print(f"[green]Deleted memory entry from:[/green] {path}")
            return

        if subcommand == "mode":
            if not remainder:
                console.print("[yellow]Use /memory mode <off|manual_only|auto>[/yellow]")
                command = registry.get_command("memory")
                if command is not None:
                    render_command_help(console, command, "mode")
                return
            settings = memory_store.update_settings(
                session_context.session_id,
                mode=remainder,
            )
            console.print(f"[green]Updated memory mode:[/green] {settings.mode}")
            return

        command = registry.get_command("memory")
        if command is not None:
            render_unknown_subcommand(console, command, subcommand)
            return

        console.print(f"[red]Unknown /memory subcommand: {subcommand}[/red]")


def _parse_memory_body(remainder: str) -> tuple[str, str, str] | None:
    if "::" not in remainder:
        return None
    head, content = remainder.split("::", 1)
    head_parts = head.strip().split(maxsplit=1)
    if len(head_parts) != 2:
        return None
    kind, title = head_parts
    content = content.strip()
    if not content:
        return None
    return kind.strip(), title.strip(), content


def _parse_daily_body(remainder: str) -> tuple[str, str] | None:
    if "::" not in remainder:
        return None
    title, content = remainder.split("::", 1)
    title = title.strip()
    content = content.strip()
    if not title or not content:
        return None
    return title, content
