"""Plan workflow slash commands."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from src.commands.common import get_plan_dependencies, prompt_for_plan_decision
from src.commands.registry import (
    CommandHelpSpec,
    CommandSubcommandHelp,
    render_command_help,
    render_unknown_subcommand,
)
from src.config import config


def register_plan_commands(registry) -> None:
    """Register `/plan`."""
    plan_help_spec = CommandHelpSpec(
        summary="Start, inspect, approve, or clear the session-local planning workflow.",
        usage=[
            "/plan",
            "/plan start <task>",
            "/plan show",
            "/plan apply",
            "/plan exit",
            "/plan clear",
        ],
        examples=[
            "/plan",
            "/plan start add a safe plan mode to the CLI",
            "/plan show",
            "/plan apply",
        ],
        notes=[
            "/plan start enters planning mode and runs a planning turn immediately.",
            "Accepting a submitted plan switches back to build mode and executes it immediately.",
            "Rejecting a submitted plan returns to build mode but keeps the plan file on disk.",
        ],
        subcommands=[
            CommandSubcommandHelp(
                name="start",
                usage="/plan start <task>",
                description="Enter planning mode, create or reuse the canonical session plan file, and run a planning turn.",
                examples=["/plan start redesign the context command and list test coverage"],
            ),
            CommandSubcommandHelp(
                name="show",
                usage="/plan show",
                description="Show the current session plan metadata and persisted plan content.",
                examples=["/plan show"],
            ),
            CommandSubcommandHelp(
                name="apply",
                usage="/plan apply",
                description="Apply the current reviewed or approved plan and execute it in build mode.",
                examples=["/plan apply"],
            ),
            CommandSubcommandHelp(
                name="exit",
                usage="/plan exit",
                description="Leave planning mode and return to build mode without executing the current plan.",
                examples=["/plan exit"],
            ),
            CommandSubcommandHelp(
                name="clear",
                usage="/plan clear",
                description="Clear the active approved plan contract while leaving the plan artifact on disk.",
                examples=["/plan clear"],
            ),
        ],
    )

    @registry.register(
        "plan",
        "Start, inspect, or apply the session-local planning workflow",
        args_description="[start <task>|show|apply|exit|clear]",
        short_desc="Manage the planning workflow",
        help_spec=plan_help_spec,
    )
    def cmd_plan(console: Console, args: str, context: Any):
        """Start, inspect, or apply the session-local planning workflow."""
        session_runtime, run_agent_turn_callback = get_plan_dependencies(console, context)
        if session_runtime is None or run_agent_turn_callback is None:
            return
        session_context = session_runtime.session_context
        runtime_config = getattr(session_runtime, "runtime_config", config)

        if not runtime_config.plan.enabled:
            console.print("[yellow]Plan mode is disabled in the current configuration[/yellow]")
            return

        raw_args = args.strip()
        command = registry.get_command("plan")
        current_plan = session_context.get_current_plan()

        if not raw_args:
            active_plan = session_context.get_active_approved_plan()
            status_lines = [
                f"[bold]Mode:[/bold] {session_context.get_session_mode()}",
                f"[bold]Plan present:[/bold] {'yes' if current_plan is not None else 'no'}",
                f"[bold]Active contract:[/bold] {'yes' if active_plan is not None else 'no'}",
            ]
            if current_plan is not None:
                status_lines.extend(
                    [
                        f"[bold]Plan ID:[/bold] {current_plan.plan_id}",
                        f"[bold]Status:[/bold] {current_plan.status}",
                        f"[bold]Path:[/bold] {current_plan.file_path}",
                        f"[bold]Task:[/bold] {current_plan.task}",
                    ]
                )
            console.print(Panel("\n".join(status_lines), title="Plan Status", border_style="cyan"))
            return

        parts = raw_args.split(maxsplit=1)
        subcommand = parts[0].lower()
        remainder = parts[1].strip() if len(parts) > 1 else ""

        if subcommand == "start":
            if not remainder:
                console.print("[yellow]Missing task for /plan start[/yellow]")
                if command is not None:
                    render_command_help(console, command, "start")
                return

            plan = session_runtime.start_planning(remainder)

            console.print(Panel(
                f"[bold]Planning task:[/bold] {plan.task}\n[bold]Plan file:[/bold] {plan.file_path}",
                title="Planning Mode",
                border_style="cyan",
            ))
            run_agent_turn_callback(plan.task)

            submitted_plan = session_context.get_current_plan()
            if submitted_plan is None:
                console.print("[yellow]Planning finished without creating a plan artifact[/yellow]")
                session_runtime.exit_plan_mode()
                return

            if submitted_plan.status != "ready_for_review":
                console.print(
                    "[yellow]Planning finished without submit_plan. "
                    "Review the plan draft with /plan show or continue planning.[/yellow]"
                )
                return

            if submitted_plan.report:
                console.print()
                console.print(Panel(Markdown(submitted_plan.report), title="Plan Review", border_style="cyan"))

            decision = prompt_for_plan_decision(console, context)
            if decision is None:
                session_runtime.exit_plan_mode()
                console.print(
                    "[yellow]Plan ready for review. Use /plan apply to execute it or /plan exit to leave planning mode.[/yellow]"
                )
                return

            if decision:
                _, execution_message = session_runtime.prepare_current_plan_for_execution()
                console.print("[green]Plan accepted. Executing the approved plan.[/green]")
                run_agent_turn_callback(execution_message)
                return

            session_runtime.mark_current_plan_rejected()
            session_runtime.exit_plan_mode()
            console.print("[yellow]Plan rejected. Returned to build mode.[/yellow]")
            return

        if subcommand == "show":
            if current_plan is None:
                console.print("[yellow]No session plan exists yet[/yellow]")
                return

            metadata = "\n".join(
                [
                    f"[bold]Plan ID:[/bold] {current_plan.plan_id}",
                    f"[bold]Status:[/bold] {current_plan.status}",
                    f"[bold]Task:[/bold] {current_plan.task}",
                    f"[bold]Path:[/bold] {current_plan.file_path}",
                    f"[bold]Approved:[/bold] {current_plan.approved_at or '-'}",
                ]
            )
            console.print(Panel(metadata, title="Session Plan", border_style="cyan"))
            if current_plan.summary:
                console.print(f"[bold]Summary:[/bold] {current_plan.summary}")
            if current_plan.content:
                console.print(Markdown(current_plan.content))
            else:
                console.print("[yellow]Plan file is currently empty[/yellow]")
            return

        if subcommand == "apply":
            if current_plan is None:
                console.print("[yellow]No session plan exists yet[/yellow]")
                return
            if current_plan.status not in {"ready_for_review", "approved", "executing"}:
                console.print(
                    f"[yellow]Current plan status '{current_plan.status}' cannot be applied[/yellow]"
                )
                return

            _, execution_message = session_runtime.prepare_current_plan_for_execution()
            console.print("[green]Executing the approved session plan.[/green]")
            run_agent_turn_callback(execution_message)
            return

        if subcommand == "exit":
            session_runtime.exit_plan_mode()
            console.print("[yellow]Returned to build mode.[/yellow]")
            return

        if subcommand == "clear":
            if current_plan is None or session_context.active_approved_plan_id is None:
                console.print("[yellow]No active approved plan contract to clear[/yellow]")
                return
            session_runtime.clear_active_plan_contract()
            console.print("[yellow]Cleared the active approved plan contract for this session.[/yellow]")
            return

        if command is not None:
            render_unknown_subcommand(console, command, subcommand)
            return

        console.print(f"[red]Unknown /plan subcommand: {subcommand}[/red]")
