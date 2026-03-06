"""Admin collectors that adapt in-memory session runtime snapshots."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from src.server.admin_redaction import preview_text
from src.server.admin_schemas import build_list_resource, build_resource


def collect_runtimes(app: FastAPI) -> dict[str, Any]:
    """Collect SessionRuntimeList resources."""
    runtime_snapshots = app.state.session_registry.snapshot_all_runtimes()
    items = [
        _build_runtime_resource(session_id, snapshot)
        for session_id, snapshot in sorted(runtime_snapshots.items())
    ]
    return build_list_resource(kind="SessionRuntime", items=items)


def collect_runtime_detail(app: FastAPI, session_id: str) -> dict[str, Any]:
    """Collect one SessionRuntime resource."""
    runtime_snapshot = app.state.session_registry.snapshot_runtime(session_id)
    if runtime_snapshot is None:
        return _build_not_loaded_runtime_resource(session_id)
    return _build_runtime_resource(session_id, runtime_snapshot)


def collect_agent_runtime(app: FastAPI, session_id: str) -> dict[str, Any]:
    """Collect one AgentRuntime resource for a session runtime."""
    snapshot = app.state.session_registry.snapshot_runtime(session_id)
    if snapshot is None:
        return build_resource(
            kind="AgentRuntime",
            name=session_id,
            spec={"session_id": session_id},
            status={"phase": "NotLoaded"},
        )
    return build_resource(
        kind="AgentRuntime",
        name=session_id,
        spec={
            "provider": snapshot["agent"]["provider"],
            "model": snapshot["agent"]["model"],
            "session_mode": snapshot["context"]["session_mode"],
            "cwd": snapshot["context"]["cwd"],
        },
        status={
            "phase": snapshot["phase"],
            "busy": snapshot["busy"],
            "active_turn_id": snapshot["active_turn_id"],
            "context_message_count": snapshot["context"]["message_count"],
            "summary_present": snapshot["context"]["summary_present"],
            "request_metrics": snapshot["agent"]["request_metrics"],
        },
    )


def collect_tools(app: FastAPI, session_id: str | None) -> dict[str, Any]:
    """Collect ToolRegistryState resources."""
    items: list[dict[str, Any]] = []
    for runtime_session_id, runtime_snapshot in _iter_runtime_snapshots(app, session_id):
        items.append(
            build_resource(
                kind="ToolRegistryState",
                name=runtime_session_id,
                spec={"session_id": runtime_session_id},
                status={
                    "phase": runtime_snapshot["phase"],
                    "tool_count": len(runtime_snapshot["tools"]),
                    "tools": runtime_snapshot["tools"],
                },
            )
        )
    if session_id and not items:
        items.append(
            build_resource(
                kind="ToolRegistryState",
                name=session_id,
                spec={"session_id": session_id},
                status={"phase": "NotLoaded", "tool_count": 0, "tools": []},
            )
        )
    return build_list_resource(kind="ToolRegistryState", items=items)


def collect_skills(app: FastAPI, session_id: str | None) -> dict[str, Any]:
    """Collect SkillCatalogState resources."""
    items: list[dict[str, Any]] = []
    for runtime_session_id, runtime_snapshot in _iter_runtime_snapshots(app, session_id):
        skill_state = runtime_snapshot["skills"]
        items.append(
            build_resource(
                kind="SkillCatalogState",
                name=runtime_session_id,
                spec={"session_id": runtime_session_id},
                status={
                    "phase": runtime_snapshot["phase"],
                    "active_skills": runtime_snapshot["context"]["active_skills"],
                    "skills": skill_state["skills"],
                    "warnings": skill_state["warnings"],
                },
            )
        )
    if session_id and not items:
        items.append(
            build_resource(
                kind="SkillCatalogState",
                name=session_id,
                spec={"session_id": session_id},
                status={"phase": "NotLoaded", "active_skills": [], "skills": [], "warnings": []},
            )
        )
    return build_list_resource(kind="SkillCatalogState", items=items)


def collect_mcp(app: FastAPI, session_id: str | None) -> dict[str, Any]:
    """Collect MCPServerState resources."""
    items: list[dict[str, Any]] = []
    for runtime_session_id, runtime_snapshot in _iter_runtime_snapshots(app, session_id):
        mcp_state = runtime_snapshot["mcp"]
        items.append(
            build_resource(
                kind="MCPServerState",
                name=runtime_session_id,
                spec={"session_id": runtime_session_id, "enabled": mcp_state["enabled"]},
                status={
                    "phase": runtime_snapshot["phase"],
                    "server_count": len(mcp_state["servers"]),
                    "servers": mcp_state["servers"],
                },
            )
        )
    if session_id and not items:
        items.append(
            build_resource(
                kind="MCPServerState",
                name=session_id,
                spec={"session_id": session_id, "enabled": False},
                status={"phase": "NotLoaded", "server_count": 0, "servers": []},
            )
        )
    return build_list_resource(kind="MCPServerState", items=items)


def collect_subagents(app: FastAPI, session_id: str | None) -> dict[str, Any]:
    """Collect SubagentRun resources."""
    items: list[dict[str, Any]] = []
    for runtime_session_id, runtime_snapshot in _iter_runtime_snapshots(app, session_id):
        for run in runtime_snapshot["subagents"]:
            items.append(
                build_resource(
                    kind="SubagentRun",
                    name=run["subagent_id"],
                    spec={
                        "session_id": runtime_session_id,
                        "label": run["label"],
                        "task": preview_text(run["task"], redacted=True),
                        "parent_turn_id": run["parent_turn_id"],
                    },
                    status={
                        "phase": run["status"],
                        "started_at": run["started_at"],
                        "ended_at": run["ended_at"],
                        "duration_s": run["duration_s"],
                        "summary": preview_text(run["summary"], redacted=True),
                    },
                    metadata_extra={"sessionId": runtime_session_id},
                )
            )
    return build_list_resource(kind="SubagentRun", items=items)


def collect_log_sessions(app: FastAPI, session_id: str | None) -> dict[str, Any]:
    """Collect LogSession resources."""
    items: list[dict[str, Any]] = []
    runtime_session_ids = set()
    for runtime_session_id, runtime_snapshot in _iter_runtime_snapshots(app, session_id):
        runtime_session_ids.add(runtime_session_id)
        logger = runtime_snapshot["logger"]
        items.append(
            build_resource(
                kind="LogSession",
                name=runtime_session_id,
                spec={
                    "session_id": runtime_session_id,
                    "session_dir": logger["session_dir"],
                    "llm_log": logger["llm_log"],
                    "events_log": logger["events_log"],
                },
                status={
                    "phase": runtime_snapshot["phase"],
                    "llm_call_count": logger["llm_call_count"],
                    "tool_call_count": logger["tool_call_count"],
                    "tools_used": logger["tools_used"],
                },
            )
        )

    if session_id and session_id not in runtime_session_ids:
        items.append(
            build_resource(
                kind="LogSession",
                name=session_id,
                spec={"session_id": session_id},
                status={"phase": "NotLoaded"},
            )
        )

    return build_list_resource(kind="LogSession", items=items)


def _iter_runtime_snapshots(app: FastAPI, session_id: str | None):
    snapshots = app.state.session_registry.snapshot_all_runtimes()
    if session_id:
        snapshot = snapshots.get(session_id)
        if snapshot is None:
            return []
        return [(session_id, snapshot)]
    return sorted(snapshots.items(), key=lambda item: item[0])


def _build_runtime_resource(session_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    return build_resource(
        kind="SessionRuntime",
        name=session_id,
        spec={
            "thread_name": snapshot["thread_name"],
            "thread_alive": snapshot["thread_alive"],
            "queue_limit": 1,
            "queue_depth": snapshot["queue_depth"],
            "pending_turn_count": snapshot["pending_turn_count"],
        },
        status={
            "phase": snapshot["phase"],
            "busy": snapshot["busy"],
            "closed": snapshot["closed"],
            "active_turn_id": snapshot["active_turn_id"],
        },
    )


def _build_not_loaded_runtime_resource(session_id: str) -> dict[str, Any]:
    return build_resource(
        kind="SessionRuntime",
        name=session_id,
        spec={"queue_limit": 1, "queue_depth": 0, "pending_turn_count": 0},
        status={"phase": "NotLoaded", "busy": False, "closed": False, "active_turn_id": None},
    )
