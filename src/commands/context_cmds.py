"""Context-inspection and compaction slash commands."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.commands.common import format_percentage, get_compaction_dependencies
from src.commands.registry import (
    CommandHelpSpec,
    CommandSubcommandHelp,
    render_command_help,
    render_unknown_subcommand,
)
from src.context_usage import build_context_usage_snapshot, format_token_count


def register_context_commands(registry) -> None:
    """Register `/context` and `/compact`."""
    context_help_spec = CommandHelpSpec(
        summary="Show an estimated next-call baseline for context usage in the current session.",
        usage=["/context"],
        examples=["/context"],
        notes=[
            "Baseline excludes the next user message.",
            "Baseline excludes any explicit $skill preload for a future turn.",
            "Token counts are rough estimates, not tokenizer-exact values.",
        ],
    )

    compact_help_spec = CommandHelpSpec(
        summary="Inspect or manage session-local context compaction for the current session.",
        usage=[
            "/compact",
            "/compact show",
            "/compact now",
            "/compact auto on",
            "/compact auto off",
        ],
        examples=["/compact", "/compact help now", "/compact auto off"],
        notes=[
            "Compaction is session-local.",
            "There is no restore or reset command in v1.",
            "/compact without arguments shows status.",
        ],
        subcommands=[
            CommandSubcommandHelp(
                name="show",
                usage="/compact show",
                description="Show the current rolling summary and its metadata.",
                examples=["/compact show"],
            ),
            CommandSubcommandHelp(
                name="now",
                usage="/compact now",
                description="Compact immediately using the current session state.",
                examples=["/compact now"],
            ),
            CommandSubcommandHelp(
                name="auto on",
                usage="/compact auto on",
                description="Enable auto-compaction for the current session.",
                examples=["/compact auto on"],
            ),
            CommandSubcommandHelp(
                name="auto off",
                usage="/compact auto off",
                description="Disable auto-compaction for the current session.",
                examples=["/compact auto off"],
            ),
        ],
    )

    @registry.register(
        "context",
        "Show estimated context usage for the next LLM call",
        args_description="",
        short_desc="Show context usage",
        help_spec=context_help_spec,
    )
    def cmd_context(console: Console, args: str, context: Any):
        """Show a repo-native next-call context usage estimate."""
        agent = context.get("agent")
        session_context = context.get("session_context")
        skill_manager = context.get("skill_manager")

        if agent is None or session_context is None:
            console.print("[red]Agent and session context are required for /context[/red]")
            return

        snapshot = build_context_usage_snapshot(agent, session_context, skill_manager)

        total_text = format_token_count(snapshot.context_window)
        used_text = format_token_count(snapshot.used_tokens)
        percentage_text = format_percentage(snapshot.used_percentage)
        summary_body = (
            f"[bold]Model:[/bold] {snapshot.model}\n"
            f"[bold]Tokens:[/bold] {used_text} / {total_text} ({percentage_text})"
        )
        console.print(Panel(summary_body, title="Context Usage", border_style="cyan"))

        category_table = Table(title="Estimated usage by category", show_header=True, header_style="bold cyan")
        category_table.add_column("Category", style="white", width=20)
        category_table.add_column("Tokens", style="green", width=10)
        category_table.add_column("Percentage", style="magenta", width=10)

        for row in snapshot.categories:
            category_table.add_row(
                row.category,
                format_token_count(row.tokens),
                format_percentage(row.percentage),
            )

        console.print(category_table)

        if snapshot.tools:
            tool_table = Table(title="Tool Schemas", show_header=True, header_style="bold cyan")
            tool_table.add_column("Tool", style="green", width=30)
            tool_table.add_column("Kind", style="white", width=18)
            tool_table.add_column("Tokens", style="magenta", width=10)
            for row in snapshot.tools:
                tool_table.add_row(row.name, row.kind, format_token_count(row.tokens))
            console.print(tool_table)

        skill_rows = [row for row in snapshot.skills if row.tokens > 0]
        if skill_rows:
            skill_table = Table(title="Skills", show_header=True, header_style="bold cyan")
            skill_table.add_column("Skill", style="green", width=25)
            skill_table.add_column("Source", style="white", width=10)
            skill_table.add_column("Usage", style="cyan", width=10)
            skill_table.add_column("Tokens", style="magenta", width=10)
            for row in skill_rows:
                skill_table.add_row(row.name, row.source, row.usage_type, format_token_count(row.tokens))
            console.print(skill_table)

        if snapshot.messages:
            message_table = Table(title="Messages", show_header=True, header_style="bold cyan")
            message_table.add_column("#", style="white", width=4)
            message_table.add_column("Role", style="green", width=10)
            message_table.add_column("Tokens", style="magenta", width=10)
            message_table.add_column("Preview", style="white", width=60)
            for row in snapshot.messages:
                message_table.add_row(str(row.index), row.role, format_token_count(row.tokens), row.preview)
            console.print(message_table)

        for note in snapshot.notes:
            console.print(f"[dim]{note}[/dim]")

    @registry.register(
        "compact",
        "Inspect or manage session-local context compaction",
        args_description="[show|now|auto on|auto off]",
        short_desc="Manage context compaction",
        help_spec=compact_help_spec,
    )
    def cmd_compact(console: Console, args: str, context: Any):
        """Inspect or manage rolling context compaction."""
        agent, session_context, compaction_manager = get_compaction_dependencies(console, context)
        if agent is None or session_context is None or compaction_manager is None:
            return

        raw_args = args.strip()
        parts = raw_args.split()

        def render_status() -> None:
            snapshot = compaction_manager.render_status_snapshot(agent)
            context_window = snapshot["context_window"]
            total_text = format_token_count(context_window)
            used_text = format_token_count(snapshot["current_used_tokens"])
            percentage_text = format_percentage(snapshot["current_used_percentage"])
            body = "\n".join([
                f"[bold]Auto-compaction:[/bold] {'on' if snapshot['auto_compaction_enabled'] else 'off'}",
                f"[bold]Configured by config:[/bold] {'on' if snapshot['configured_auto_compact'] else 'off'}",
                f"[bold]Threshold:[/bold] {snapshot['auto_compact_threshold'] * 100:.0f}%",
                f"[bold]Target after compaction:[/bold] {snapshot['target_usage_after_compaction'] * 100:.0f}%",
                f"[bold]Configured recent turns retained:[/bold] {snapshot['min_recent_turns']}",
                f"[bold]Effective recent turns retained:[/bold] {snapshot['effective_retained_turns']}",
                f"[bold]Current baseline:[/bold] {used_text} / {total_text} ({percentage_text})",
                (
                    f"[bold]Auto decision:[/bold] "
                    f"{'compact now' if snapshot['decision_should_compact'] else 'skip'} "
                    f"({snapshot['decision_reason']})"
                ),
                f"[bold]Decision detail:[/bold] {snapshot['decision_reason_text']}",
                f"[bold]Summary present:[/bold] {'yes' if snapshot['summary_present'] else 'no'}",
                f"[bold]Summary-covered turns:[/bold] {snapshot['summary_covered_turn_count']}",
                f"[bold]Raw retained turns:[/bold] {snapshot['raw_retained_turn_count']}",
            ])
            console.print(Panel(body, title="Context Compaction", border_style="cyan"))

        def render_manual_steps(plan, details: dict[str, Any], result) -> None:
            complete_turns = details.get("complete_turn_count", 0)
            evictable_turns = details.get("evictable_turn_count", 0)
            configured_retained = details.get(
                "configured_min_recent_turns",
                compaction_manager.policy.min_recent_turns,
            )
            effective_retained = details.get("effective_retained_turns", len(plan.retained_turns))
            baseline_tokens = format_token_count(details.get("current_used_tokens"))

            lines = [
                "[bold]Manual compaction steps[/bold]",
                (
                    "1. Inspect current context: "
                    f"{complete_turns} complete turn(s), baseline {baseline_tokens}."
                ),
                (
                    "2. Apply adaptive retention: "
                    f"keep {effective_retained} raw turn(s) "
                    f"(configured cap {configured_retained}), "
                    f"leaving {evictable_turns} evictable older turn(s)."
                ),
            ]

            if result.status == "skipped":
                lines.append(
                    "3. Stop: "
                    f"{compaction_manager.describe_reason(result.reason, result.details)}"
                )
            else:
                lines.append(
                    "3. Select turns to summarize: "
                    f"force mode compacts all {len(plan.turns_to_compact)} evictable older turn(s)."
                )
                if result.error:
                    lines.append(
                        "4. Summary generation failed, so Nano-Claw used the deterministic fallback summary."
                    )
                else:
                    lines.append(
                        "4. Generate or update the rolling summary with the configured model."
                    )
                lines.append(
                    "5. Replace older raw history with the retained recent turns and store the new summary."
                )
                lines.append(
                    "6. Recalculate baseline usage: "
                    f"{format_token_count(result.before_tokens)} -> {format_token_count(result.after_tokens)}."
                )

            console.print(Panel("\n".join(lines), title="Manual Compaction", border_style="cyan"))

        if not parts:
            render_status()
            return

        if parts[0] == "show":
            summary = session_context.get_summary()
            if summary is None:
                console.print("[yellow]No compacted summary is available for this session[/yellow]")
                return

            metadata = "\n".join([
                f"[bold]Updated:[/bold] {summary.updated_at}",
                f"[bold]Compactions:[/bold] {summary.compaction_count}",
                f"[bold]Covered turns:[/bold] {summary.covered_turn_count}",
                f"[bold]Covered messages:[/bold] {summary.covered_message_count}",
            ])
            console.print(Panel(metadata, title="Compacted Summary", border_style="cyan"))
            console.print(summary.rendered_text)
            return

        if parts[0] == "now":
            logger = getattr(agent, "logger", None)
            plan_preview = compaction_manager._build_plan(agent, force=True)
            preview_details = compaction_manager._build_debug_details(
                plan_preview,
                current_used_tokens=plan_preview.before_tokens,
                threshold_tokens=(
                    int(plan_preview.context_window * compaction_manager.policy.auto_compact_threshold)
                    if plan_preview.context_window is not None
                    else None
                ),
                force=True,
            )
            if logger is not None:
                logger.log_context_compaction_event(
                    turn_id=None,
                    stage="started",
                    reason="manual_command",
                    covered_turn_count=len(plan_preview.turns_to_compact),
                    retained_turn_count=len(plan_preview.retained_turns),
                    **preview_details,
                )

            result = compaction_manager.compact_now(agent, "manual_command", force=True)
            if result.status == "skipped":
                if logger is not None:
                    logger.log_context_compaction_event(
                        turn_id=None,
                        stage="skipped",
                        reason=result.reason,
                        reason_text=compaction_manager.describe_reason(result.reason, result.details),
                        **result.details,
                    )
                console.print(
                    "[yellow]Context compaction skipped:[/yellow] "
                    f"{compaction_manager.describe_reason(result.reason, result.details)}"
                )
                render_manual_steps(plan_preview, result.details, result)
                for line in compaction_manager.render_debug_lines(result.details):
                    console.print(f"[dim]{line}[/dim]")
                return

            if result.error:
                if logger is not None:
                    logger.log_context_compaction_event(
                        turn_id=None,
                        stage="failed",
                        reason=result.reason,
                        error=result.error,
                        **result.details,
                    )
                console.print(f"[yellow]Compaction used fallback summary:[/yellow] {result.error}")
            elif logger is not None:
                logger.log_context_compaction_event(
                    turn_id=None,
                    stage="completed",
                    reason=result.reason,
                    covered_turn_count=result.covered_turn_count,
                    retained_turn_count=result.retained_turn_count,
                    before_tokens=result.before_tokens,
                    after_tokens=result.after_tokens,
                    **result.details,
                )

            render_manual_steps(plan_preview, result.details, result)
            console.print(
                "[green]Context compacted:[/green] "
                f"{result.covered_turn_count} turns summarized, "
                f"{result.retained_turn_count} recent turns kept, "
                f"{format_token_count(result.before_tokens)} -> {format_token_count(result.after_tokens)}"
            )
            return

        if parts[0] == "auto" and len(parts) == 2:
            if parts[1] == "on":
                session_context.set_auto_compaction(True)
                console.print("[green]Auto-compaction enabled for this session[/green]")
                return
            if parts[1] == "off":
                session_context.set_auto_compaction(False)
                console.print("[green]Auto-compaction disabled for this session[/green]")
                return

        command = registry.get_command("compact")
        if parts[0] == "auto" and command is not None:
            console.print("[yellow]Missing /compact auto action: expected on or off[/yellow]")
            render_command_help(console, command, "auto")
            return

        if command is not None:
            render_unknown_subcommand(console, command, parts[0])
            return

        console.print(f"[red]Unknown /compact subcommand: {parts[0]}[/red]")
