"""SSE helpers for the admin console."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from time import time
from typing import Any

from fastapi import FastAPI, Request

from src.server.admin_collectors_core import (
    collect_config_view,
    collect_event_bus_state,
    collect_server_overview,
    collect_sessions,
    collect_turns,
)
from src.server.admin_collectors_runtime import (
    collect_agent_runtime,
    collect_log_sessions,
    collect_mcp,
    collect_runtimes,
    collect_skills,
    collect_subagents,
    collect_tools,
)
from src.server.admin_schemas import new_resource_version


DEFAULT_STREAM_RESOURCES = ("overview", "sessions", "runtimes", "event-bus")
DEFAULT_STREAM_INTERVAL_MS = 1000
MIN_STREAM_INTERVAL_MS = 250
MAX_STREAM_INTERVAL_MS = 10000


def parse_stream_resources(resources: str | None) -> list[str]:
    """Parse and validate stream resource selector."""
    if not resources:
        return list(DEFAULT_STREAM_RESOURCES)
    parsed = [resource.strip() for resource in resources.split(",") if resource.strip()]
    allowed = set(_RESOURCE_FETCHERS.keys())
    selected = [resource for resource in parsed if resource in allowed]
    return selected or list(DEFAULT_STREAM_RESOURCES)


def normalize_stream_interval(interval_ms: int | None) -> int:
    """Clamp admin stream interval."""
    if interval_ms is None:
        return DEFAULT_STREAM_INTERVAL_MS
    return max(MIN_STREAM_INTERVAL_MS, min(interval_ms, MAX_STREAM_INTERVAL_MS))


async def iter_admin_events(
    app: FastAPI,
    *,
    request: Request,
    resources: list[str],
    session_id: str | None,
    interval_ms: int,
    max_events: int | None = None,
):
    """Yield admin SSE events by periodically collecting selected resources."""
    selected_interval = normalize_stream_interval(interval_ms)
    last_digests: dict[str, str] = {}
    initial = True
    emitted = 0

    while True:
        if getattr(app.state, "shutdown_requested", False):
            return
        if await request.is_disconnected():
            return
        try:
            for resource in resources:
                if getattr(app.state, "shutdown_requested", False):
                    return
                if await request.is_disconnected():
                    return
                payload = _collect_resource_payload(app, resource, session_id)
                digest = sha256(
                    json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
                envelope = {
                    "resource": resource,
                    "name": payload.get("metadata", {}).get("name", payload.get("kind", resource)),
                    "resourceVersion": payload.get("metadata", {}).get("resourceVersion", new_resource_version()),
                    "payload": payload,
                }
                if initial:
                    yield _format_sse("snapshot", envelope)
                    emitted += 1
                    if max_events is not None and emitted >= max_events:
                        return
                elif last_digests.get(resource) != digest:
                    # Admin clients use the change marker for cheap invalidation and the
                    # follow-up snapshot as the full replacement payload.
                    yield _format_sse("resource_changed", envelope)
                    emitted += 1
                    if max_events is not None and emitted >= max_events:
                        return
                    yield _format_sse("snapshot", envelope)
                    emitted += 1
                    if max_events is not None and emitted >= max_events:
                        return
                last_digests[resource] = digest

            yield _format_sse(
                "heartbeat",
                {
                    "resourceVersion": new_resource_version(),
                    "timestamp": time(),
                },
            )
            emitted += 1
            if max_events is not None and emitted >= max_events:
                return
            initial = False
            await asyncio.sleep(selected_interval / 1000.0)
        except Exception as exc:
            yield _format_sse(
                "error",
                {
                    "resourceVersion": new_resource_version(),
                    "message": str(exc) or exc.__class__.__name__,
                },
            )
            emitted += 1
            if max_events is not None and emitted >= max_events:
                return
            await asyncio.sleep(selected_interval / 1000.0)


def _collect_resource_payload(app: FastAPI, resource: str, session_id: str | None) -> dict[str, Any]:
    fetcher = _RESOURCE_FETCHERS[resource]
    return fetcher(app, session_id)


def _format_sse(event_name: str, payload: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


_RESOURCE_FETCHERS = {
    "overview": lambda app, _session_id: collect_server_overview(app),
    "sessions": lambda app, _session_id: collect_sessions(app),
    "runtimes": lambda app, _session_id: collect_runtimes(app),
    "turns": lambda app, _session_id: collect_turns(
        app,
        session_id=_session_id,
        status=None,
        cursor=None,
        limit=100,
        redacted=True,
    ),
    "event-bus": lambda app, _session_id: collect_event_bus_state(app),
    "agent-runtime": lambda app, _session_id: collect_agent_runtime(app, _session_id or ""),
    "tools": lambda app, _session_id: collect_tools(app, _session_id),
    "skills": lambda app, _session_id: collect_skills(app, _session_id),
    "mcp": lambda app, _session_id: collect_mcp(app, _session_id),
    "subagents": lambda app, _session_id: collect_subagents(app, _session_id),
    "log-sessions": lambda app, _session_id: collect_log_sessions(app, _session_id),
    "config": lambda app, _session_id: collect_config_view(app),
}
