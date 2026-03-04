"""Shared helpers for built-in slash commands."""

from __future__ import annotations

from typing import Any, Optional

from rich.console import Console
from rich.table import Table


def get_skill_dependencies(console: Console, context: Any):
    """Get skill-related dependencies from command context."""
    skill_manager = context.get("skill_manager")
    session_context = context.get("session_context")

    if not skill_manager or not session_context:
        console.print("[yellow]Skills are not initialized[/yellow]")
        return None, None

    return skill_manager, session_context


def print_skill_list(console: Console, skill_manager, session_context) -> None:
    """Render the discovered skills table."""
    skills = skill_manager.list_skills()
    if not skills:
        console.print("[yellow]No skills discovered[/yellow]")
        return

    active = set(session_context.get_active_skills())
    table = Table(title=f"Available Skills ({len(skills)})", show_header=True, header_style="bold cyan")
    table.add_column("Skill", style="green", width=20)
    table.add_column("Description", style="white", width=50)
    table.add_column("Source", style="dim", width=10)
    table.add_column("Catalog", style="magenta", width=8)
    table.add_column("Active", style="cyan", width=8)

    for skill in skills:
        table.add_row(
            skill.name,
            skill.short_description,
            skill.source,
            "yes" if skill.catalog_visible else "no",
            "yes" if skill.name in active else "no",
        )

    console.print(table)


def render_resource_inventory(paths) -> str:
    """Render a list of resource paths for display."""
    if not paths:
        return "  - none"
    return "\n".join(f"  - {path}" for path in paths)


def format_percentage(value: Optional[float]) -> str:
    """Format a percentage for context usage tables."""
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def get_compaction_dependencies(console: Console, context: Any):
    """Get context-compaction dependencies from command context."""
    agent = context.get("agent")
    session_context = context.get("session_context")
    if agent is None or session_context is None:
        console.print("[red]Agent and session context are required for /compact[/red]")
        return None, None, None

    compaction_manager = getattr(agent, "context_compaction", None)
    if compaction_manager is None:
        console.print("[red]Context compaction is not initialized[/red]")
        return None, None, None

    return agent, session_context, compaction_manager


def get_subagent_dependencies(console: Console, context: Any):
    """Get subagent-related dependencies from command context."""
    agent = context.get("agent")
    subagent_manager = context.get("subagent_manager")
    if agent is None or subagent_manager is None:
        console.print("[red]Agent and subagent manager are required for /subagent[/red]")
        return None, None
    return agent, subagent_manager


def get_plan_dependencies(console: Console, context: Any):
    """Get plan-workflow dependencies from command context."""
    session_runtime = context.get("session_runtime_controller")
    run_agent_turn = context.get("run_agent_turn_callback")
    if session_runtime is None or run_agent_turn is None:
        console.print("[red]Session runtime controller and turn callback are required for /plan[/red]")
        return None, None
    return session_runtime, run_agent_turn


def prompt_for_plan_decision(console: Console, context: Any) -> Optional[bool]:
    """Prompt for plan approval when an interactive input callback is available."""
    prompt_input = context.get("prompt_input_callback")
    if prompt_input is None or not console.is_terminal:
        return None

    while True:
        decision = prompt_input("\nPlan Review [accept/reject] > ").strip().lower()
        if decision in {"accept", "a", "yes", "y"}:
            return True
        if decision in {"reject", "r", "no", "n"}:
            return False
        console.print("[yellow]Please enter accept or reject[/yellow]")
