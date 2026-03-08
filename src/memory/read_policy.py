"""Default retrieval policies for the agent-facing memory_search tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.memory.types import MemorySearchPlan

if TYPE_CHECKING:
    from src.memory.session_memory import SessionMemory


def build_search_plan(
    memory: "SessionMemory",
    session_id: str,
    *,
    query: str,
    limit: int | None = None,
    include_daily: bool | None = None,
    include_inactive: bool | None = None,
) -> MemorySearchPlan:
    """Resolve the default memory_search behavior for one session."""
    settings = memory.get_settings(session_id)
    policy_name = settings.read_policy
    configured_limit = memory.runtime_config.memory.max_search_results
    requested_limit = configured_limit if limit is None else int(limit)
    resolved_limit = max(1, min(requested_limit, configured_limit))

    if include_daily is None:
        resolved_include_daily = policy_name != "curated_only"
        recent_daily_days = (
            memory.runtime_config.memory.recent_daily_days
            if policy_name == "curated_plus_recent_daily"
            else None
        )
    else:
        resolved_include_daily = bool(include_daily)
        recent_daily_days = None

    if include_inactive is None:
        resolved_include_inactive = False
    else:
        resolved_include_inactive = bool(include_inactive)

    return MemorySearchPlan(
        policy_name=policy_name,
        query=query,
        limit=resolved_limit,
        include_daily=resolved_include_daily,
        include_inactive=resolved_include_inactive,
        recent_daily_days=recent_daily_days if resolved_include_daily else None,
    )
