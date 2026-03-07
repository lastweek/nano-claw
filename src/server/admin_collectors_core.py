"""Core admin resource collectors that read app/store state directly."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import FastAPI

from src.server.admin_redaction import preview_text, redact_config_object
from src.server.admin_schemas import build_list_resource, build_resource
from src.store.repository import AppStore, TurnRecord
from src.utils import resolve_path


def collect_server_overview(app: FastAPI) -> dict[str, Any]:
    """Collect one ServerOverview resource."""
    store: AppStore = app.state.store
    sessions = store.list_sessions()
    session_state_counts: dict[str, int] = {}
    for session in sessions:
        session_state_counts[session.state] = session_state_counts.get(session.state, 0) + 1

    turn_status_counts = store.count_turns_by_status()
    runtime_snapshots = app.state.session_registry.snapshot_all_runtimes()
    busy_runtime_count = sum(1 for snapshot in runtime_snapshots.values() if snapshot.get("busy"))
    started_at = getattr(app.state, "started_at", None)
    uptime_seconds = None
    if isinstance(started_at, datetime):
        uptime_seconds = max((datetime.now() - started_at).total_seconds(), 0.0)

    return build_resource(
        kind="ServerOverview",
        name="local",
        spec={
            "repo_root": str(app.state.repo_root),
            "db_path": str(store.db_path),
            "log_root": str(resolve_path(app.state.runtime_config.logging.log_dir, app.state.repo_root)),
            "started_at": started_at.isoformat() if isinstance(started_at, datetime) else None,
        },
        status={
            "phase": "Running",
            "uptime_seconds": round(uptime_seconds, 3) if uptime_seconds is not None else None,
            "session_count": len(sessions),
            "session_state_counts": session_state_counts,
            "runtime_count": len(runtime_snapshots),
            "busy_runtime_count": busy_runtime_count,
            "turn_status_counts": turn_status_counts,
        },
    )


def collect_sessions(app: FastAPI) -> dict[str, Any]:
    """Collect SessionList resources."""
    store: AppStore = app.state.store
    message_counts = store.count_messages_by_session()
    turn_counts = store.count_turns_by_session()
    items: list[dict[str, Any]] = []
    for session in store.list_sessions():
        items.append(
            build_resource(
                kind="Session",
                name=session.id,
                spec={
                    "title": session.title,
                    "summary_text": preview_text(session.summary_text, redacted=True),
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "closed_at": session.closed_at,
                },
                status={
                    "phase": "Active" if session.state == "active" else "Closed",
                    "state": session.state,
                    "message_count": message_counts.get(session.id, 0),
                    "turn_count": turn_counts.get(session.id, 0),
                },
            )
        )
    return build_list_resource(kind="Session", items=items)


def collect_session_detail(app: FastAPI, session_id: str) -> dict[str, Any] | None:
    """Collect one Session resource detail."""
    store: AppStore = app.state.store
    detail = store.get_session_detail(session_id)
    if detail is None:
        return None
    runtime_snapshot = app.state.session_registry.snapshot_runtime(session_id)
    return build_resource(
        kind="Session",
        name=detail.session.id,
        spec={
            "title": detail.session.title,
            "summary_text": preview_text(detail.session.summary_text, redacted=True),
            "created_at": detail.session.created_at,
            "updated_at": detail.session.updated_at,
            "closed_at": detail.session.closed_at,
            "messages": [
                {"seq": message.seq, "role": message.role, "content": preview_text(message.content, redacted=True)}
                for message in detail.messages
            ],
        },
        status={
            "phase": "Active" if detail.session.state == "active" else "Closed",
            "state": detail.session.state,
            "busy": bool(runtime_snapshot and runtime_snapshot.get("busy")),
            "recent_turns": [
                {
                    "id": turn.id,
                    "status": turn.status,
                    "created_at": turn.created_at,
                    "ended_at": turn.ended_at,
                    "input_text": preview_text(turn.input_text, redacted=True),
                }
                for turn in detail.recent_turns
            ],
        },
    )


def collect_turns(
    app: FastAPI,
    *,
    session_id: str | None,
    status: str | None,
    cursor: str | None,
    limit: int,
    redacted: bool = True,
) -> dict[str, Any]:
    """Collect TurnList resources."""
    turns, next_cursor = app.state.store.list_turns(
        session_id=session_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    items = [_build_turn_resource(turn, redacted=redacted) for turn in turns]
    return build_list_resource(kind="Turn", items=items, next_cursor=next_cursor, count=len(items))


def collect_turn_detail(app: FastAPI, turn_id: str, *, redacted: bool = True) -> dict[str, Any] | None:
    """Collect one Turn resource."""
    turn = app.state.store.get_turn(turn_id)
    if turn is None:
        return None
    injected_entry_ids: list[str] = []
    memory_store = getattr(app.state, "memory_store", None)
    if memory_store is not None and memory_store.is_enabled():
        injected_entry_ids = [
            *[
                entry_id
                for event in memory_store.read_audit_log(turn.session_id)
                if event.get("event") == "prompt_injection" and event.get("turn_id") == turn_id
                for entry_id in event.get("entry_ids", [])
            ],
        ]
    return _build_turn_resource(turn, redacted=redacted, injected_entry_ids=injected_entry_ids)


def collect_event_bus_state(app: FastAPI) -> dict[str, Any]:
    """Collect one EventBusState resource."""
    snapshot = app.state.event_bus.snapshot()
    return build_resource(
        kind="EventBusState",
        name="turn-event-bus",
        spec={
            "max_subscribers_per_turn": snapshot["max_subscribers_per_turn"],
            "max_queue_size": snapshot["max_queue_size"],
            "default_heartbeat_seconds": snapshot["default_heartbeat_seconds"],
        },
        status={
            "phase": "Running",
            "tracked_turn_ids": snapshot["tracked_turn_ids"],
            "subscriber_counts": snapshot["subscriber_counts"],
            "closed_turn_count": snapshot["closed_turn_count"],
        },
    )


def collect_config_view(app: FastAPI) -> dict[str, Any]:
    """Collect sanitized runtime config view."""
    runtime_config = app.state.runtime_config
    config_payload = {
        "llm": runtime_config.llm.model_dump(),
        "logging": runtime_config.logging.model_dump(),
        "agent": runtime_config.agent.model_dump(),
        "ui": runtime_config.ui.model_dump(),
        "context": runtime_config.context.model_dump(),
        "subagents": runtime_config.subagents.model_dump(),
        "plan": runtime_config.plan.model_dump(),
        "server": runtime_config.server.model_dump(),
        "memory": runtime_config.memory.model_dump(),
        "macos_tools": runtime_config.macos_tools.model_dump(),
        "mcp": runtime_config.mcp.model_dump(),
    }
    sanitized = redact_config_object(config_payload)
    return build_resource(
        kind="ConfigView",
        name="runtime-config",
        spec={"repo_root": str(app.state.repo_root)},
        status={"phase": "Ready", "config": sanitized},
    )


def collect_memory_workspace(app: FastAPI, session_id: str) -> dict[str, Any] | None:
    """Collect one file-backed memory workspace resource."""
    session = app.state.store.get_session_record(session_id)
    if session is None:
        return None

    memory_store = app.state.memory_store
    if not memory_store.is_enabled():
        return build_resource(
            kind="SessionMemoryWorkspace",
            name=session_id,
            spec={"session_id": session_id},
            status={"phase": "Disabled"},
            metadata_extra={"sessionId": session_id},
        )

    workspace = memory_store.describe_workspace(session_id)
    return build_resource(
        kind="SessionMemoryWorkspace",
        name=session_id,
        spec={
            "session_id": session_id,
            "root_dir": workspace["root_dir"],
            "document_path": workspace["document_path"],
            "settings_path": workspace["settings_path"],
            "audit_path": workspace["audit_path"],
        },
        status={
            "phase": "Ready",
            "mode": workspace["settings"]["mode"],
            "entry_count": workspace["entry_count"],
            "section_counts": workspace["section_counts"],
            "status_counts": workspace["status_counts"],
            "daily_files": list(workspace["daily_files"]),
            "daily_file_count": len(workspace["daily_files"]),
        },
        metadata_extra={"sessionId": session_id},
    )


def collect_memory_document(app: FastAPI, session_id: str) -> dict[str, Any] | None:
    """Collect one curated memory document resource."""
    session = app.state.store.get_session_record(session_id)
    if session is None:
        return None

    memory_store = app.state.memory_store
    if not memory_store.is_enabled():
        return build_resource(
            kind="SessionMemoryDocument",
            name=f"{session_id}-memory",
            spec={"session_id": session_id, "path": None},
            status={"phase": "Disabled", "content": ""},
            metadata_extra={"sessionId": session_id},
        )

    path = memory_store.curated_document_path(session_id)
    content = memory_store.read_curated_document(session_id)
    return build_resource(
        kind="SessionMemoryDocument",
        name=f"{session_id}-memory",
        spec={
            "session_id": session_id,
            "path": str(path),
        },
        status={
            "phase": "Ready",
            "content": content,
            "content_preview": preview_text(content, redacted=False),
        },
        metadata_extra={"sessionId": session_id},
    )


def collect_memory_entries(app: FastAPI, session_id: str) -> dict[str, Any] | None:
    """Collect one list of structured curated memory entries."""
    session = app.state.store.get_session_record(session_id)
    if session is None:
        return None

    memory_store = app.state.memory_store
    if not memory_store.is_enabled():
        return build_list_resource(kind="SessionMemoryEntry", items=[], count=0)

    items = [
        build_resource(
            kind="SessionMemoryEntry",
            name=entry.entry_id,
            spec={
                "session_id": session_id,
                "kind": entry.kind,
                "title": entry.title,
                "source": entry.source,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "last_verified_at": entry.last_verified_at,
                "supersedes": entry.supersedes,
            },
            status={
                "phase": entry.status.title(),
                "status": entry.status,
                "confidence": entry.confidence,
                "content": entry.content,
                "content_preview": preview_text(entry.content, redacted=False),
            },
            metadata_extra={"sessionId": session_id},
        )
        for entry in memory_store.list_entries(session_id, include_inactive=True)
    ]
    return build_list_resource(kind="SessionMemoryEntry", items=items, count=len(items))


def collect_memory_entry(app: FastAPI, session_id: str, entry_id: str) -> dict[str, Any] | None:
    """Collect one structured curated memory entry."""
    session = app.state.store.get_session_record(session_id)
    if session is None:
        return None
    memory_store = app.state.memory_store
    if not memory_store.is_enabled():
        return build_resource(
            kind="SessionMemoryEntry",
            name=entry_id,
            spec={"session_id": session_id},
            status={"phase": "Disabled"},
            metadata_extra={"sessionId": session_id},
        )
    entry = memory_store.get_entry(session_id, entry_id)
    if entry is None:
        return None
    return build_resource(
        kind="SessionMemoryEntry",
        name=entry.entry_id,
        spec={
            "session_id": session_id,
            "kind": entry.kind,
            "title": entry.title,
            "source": entry.source,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "last_verified_at": entry.last_verified_at,
            "supersedes": entry.supersedes,
        },
        status={
            "phase": entry.status.title(),
            "status": entry.status,
            "confidence": entry.confidence,
            "content": entry.content,
            "content_preview": preview_text(entry.content, redacted=False),
        },
        metadata_extra={"sessionId": session_id},
    )


def collect_memory_settings(app: FastAPI, session_id: str) -> dict[str, Any] | None:
    """Collect one session memory settings resource."""
    session = app.state.store.get_session_record(session_id)
    if session is None:
        return None
    memory_store = app.state.memory_store
    if not memory_store.is_enabled():
        return build_resource(
            kind="SessionMemorySettings",
            name=session_id,
            spec={"session_id": session_id},
            status={"phase": "Disabled"},
            metadata_extra={"sessionId": session_id},
        )
    settings = memory_store.get_settings(session_id)
    return build_resource(
        kind="SessionMemorySettings",
        name=session_id,
        spec={
            "session_id": session_id,
            "path": str(memory_store.settings_path(session_id)),
        },
        status={
            "phase": "Ready",
            "mode": settings.mode,
            "auto_retrieve_enabled": settings.auto_retrieve_enabled,
            "manual_write_enabled": settings.manual_write_enabled,
            "autonomous_write_enabled": settings.autonomous_write_enabled,
        },
        metadata_extra={"sessionId": session_id},
    )


def collect_memory_audit(app: FastAPI, session_id: str, *, limit: int = 100) -> dict[str, Any] | None:
    """Collect recent session memory audit events."""
    session = app.state.store.get_session_record(session_id)
    if session is None:
        return None
    memory_store = app.state.memory_store
    if not memory_store.is_enabled():
        return build_list_resource(kind="SessionMemoryAuditEvent", items=[], count=0)
    events = memory_store.read_audit_log(session_id, limit=limit)
    items = [
        build_resource(
            kind="SessionMemoryAuditEvent",
            name=f"{session_id}-{index}",
            spec={
                "session_id": session_id,
                "timestamp": event.get("timestamp"),
                "event": event.get("event"),
            },
            status={
                "phase": "Ready",
                "payload": {
                    key: value
                    for key, value in event.items()
                    if key not in {"timestamp", "session_id", "event"}
                },
            },
            metadata_extra={"sessionId": session_id},
        )
        for index, event in enumerate(events, start=1)
    ]
    return build_list_resource(kind="SessionMemoryAuditEvent", items=items, count=len(items))


def collect_memory_daily_logs(app: FastAPI, session_id: str) -> dict[str, Any] | None:
    """Collect the list of daily memory log files for one session."""
    session = app.state.store.get_session_record(session_id)
    if session is None:
        return None

    memory_store = app.state.memory_store
    if not memory_store.is_enabled():
        return build_list_resource(kind="SessionMemoryDailyLog", items=[], count=0)

    items = [
        build_resource(
            kind="SessionMemoryDailyLog",
            name=f"{session_id}-{date}",
            spec={
                "session_id": session_id,
                "date": date,
                "path": str(memory_store.daily_log_path(session_id, date)),
            },
            status={"phase": "Ready"},
            metadata_extra={"sessionId": session_id, "date": date},
        )
        for date in memory_store.list_daily_logs(session_id)
    ]
    return build_list_resource(kind="SessionMemoryDailyLog", items=items, count=len(items))


def collect_memory_daily_log(app: FastAPI, session_id: str, date: str) -> dict[str, Any] | None:
    """Collect one daily memory log resource."""
    session = app.state.store.get_session_record(session_id)
    if session is None:
        return None

    memory_store = app.state.memory_store
    if not memory_store.is_enabled():
        return build_resource(
            kind="SessionMemoryDailyLog",
            name=f"{session_id}-{date}",
            spec={"session_id": session_id, "date": date, "path": None},
            status={"phase": "Disabled", "content": ""},
            metadata_extra={"sessionId": session_id, "date": date},
        )

    try:
        path = memory_store.daily_log_path(session_id, date)
        content = memory_store.read_daily_log(session_id, date)
    except (FileNotFoundError, ValueError):
        return None

    return build_resource(
        kind="SessionMemoryDailyLog",
        name=f"{session_id}-{date}",
        spec={
            "session_id": session_id,
            "date": date,
            "path": str(path),
        },
        status={
            "phase": "Ready",
            "content": content,
            "content_preview": preview_text(content, redacted=False),
        },
        metadata_extra={"sessionId": session_id, "date": date},
    )


def _build_turn_resource(
    turn: TurnRecord,
    *,
    redacted: bool,
    injected_entry_ids: list[str] | None = None,
) -> dict[str, Any]:
    turn_phase = {
        "queued": "Queued",
        "running": "Running",
        "completed": "Completed",
        "failed": "Failed",
    }.get(turn.status, "Unknown")
    return build_resource(
        kind="Turn",
        name=turn.id,
        spec={
            "session_id": turn.session_id,
            "input_text": preview_text(turn.input_text, redacted=redacted),
            "created_at": turn.created_at,
            "started_at": turn.started_at,
            "ended_at": turn.ended_at,
        },
        status={
            "phase": turn_phase,
            "status": turn.status,
            "final_output": preview_text(turn.final_output, redacted=redacted),
            "error_text": preview_text(turn.error_text, redacted=redacted),
            "updated_at": turn.updated_at,
            "memory_injected_entry_ids": list(injected_entry_ids or []),
        },
        metadata_extra={"sessionId": turn.session_id},
    )
