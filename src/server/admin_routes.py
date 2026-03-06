"""Read-only admin API and UI routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from src.server.admin_collectors_core import (
    collect_config_view,
    collect_event_bus_state,
    collect_server_overview,
    collect_session_detail,
    collect_sessions,
    collect_turn_detail,
    collect_turns,
)
from src.server.admin_collectors_runtime import (
    collect_agent_runtime,
    collect_log_sessions,
    collect_mcp,
    collect_runtime_detail,
    collect_runtimes,
    collect_skills,
    collect_subagents,
    collect_tools,
)
from src.server.admin_logs import (
    collect_log_file_tail,
    collect_log_files,
    resolve_log_file_path,
)
from src.server.admin_stream import iter_admin_events, normalize_stream_interval, parse_stream_resources


router = APIRouter()


@router.get("/admin", include_in_schema=False)
def admin_ui(request: Request):
    """Serve the admin single-page app."""
    return FileResponse(Path(request.app.state.admin_static_dir) / "index.html")


@router.get("/api/v1/admin/overview")
def admin_overview(request: Request) -> dict:
    return collect_server_overview(request.app)


@router.get("/api/v1/admin/sessions")
def admin_sessions(request: Request) -> dict:
    return collect_sessions(request.app)


@router.get("/api/v1/admin/sessions/{session_id}")
def admin_session_detail(request: Request, session_id: str) -> dict:
    resource = collect_session_detail(request.app, session_id)
    if resource is None:
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
    return resource


@router.get("/api/v1/admin/runtimes")
def admin_runtimes(request: Request) -> dict:
    return collect_runtimes(request.app)


@router.get("/api/v1/admin/runtimes/{session_id}")
def admin_runtime_detail(request: Request, session_id: str) -> dict:
    return collect_runtime_detail(request.app, session_id)


@router.get("/api/v1/admin/turns")
def admin_turns(
    request: Request,
    session_id: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    redacted: bool = True,
) -> dict:
    return collect_turns(
        request.app,
        session_id=session_id,
        status=status,
        cursor=cursor,
        limit=limit,
        redacted=redacted,
    )


@router.get("/api/v1/admin/turns/{turn_id}")
def admin_turn_detail(request: Request, turn_id: str, redacted: bool = True) -> dict:
    resource = collect_turn_detail(request.app, turn_id, redacted=redacted)
    if resource is None:
        raise HTTPException(status_code=404, detail=f"Unknown turn: {turn_id}")
    return resource


@router.get("/api/v1/admin/event-bus")
def admin_event_bus(request: Request) -> dict:
    return collect_event_bus_state(request.app)


@router.get("/api/v1/admin/agent-runtimes/{session_id}")
def admin_agent_runtime(request: Request, session_id: str) -> dict:
    return collect_agent_runtime(request.app, session_id)


@router.get("/api/v1/admin/tools")
def admin_tools(request: Request, session_id: str | None = None) -> dict:
    return collect_tools(request.app, session_id)


@router.get("/api/v1/admin/skills")
def admin_skills(request: Request, session_id: str | None = None) -> dict:
    return collect_skills(request.app, session_id)


@router.get("/api/v1/admin/mcp")
def admin_mcp(request: Request, session_id: str | None = None) -> dict:
    return collect_mcp(request.app, session_id)


@router.get("/api/v1/admin/subagents")
def admin_subagents(request: Request, session_id: str | None = None) -> dict:
    return collect_subagents(request.app, session_id)


@router.get("/api/v1/admin/log-sessions")
def admin_log_sessions(request: Request, session_id: str | None = None) -> dict:
    return collect_log_sessions(request.app, session_id)


@router.get("/api/v1/admin/log-files")
def admin_log_files(
    request: Request,
    session_id: str,
    path: str | None = None,
) -> dict:
    try:
        return collect_log_files(request.app, session_id, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/admin/log-files/tail")
def admin_log_tail(
    request: Request,
    session_id: str,
    file: str,
    lines: int = Query(default=200, ge=1, le=2000),
    redacted: bool = True,
) -> dict:
    try:
        return collect_log_file_tail(
            request.app,
            session_id=session_id,
            file_path=file,
            lines=lines,
            redacted=redacted,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/admin/log-files/download")
def admin_log_download(request: Request, session_id: str, file: str) -> FileResponse:
    try:
        target = resolve_log_file_path(request.app, session_id=session_id, file_path=file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=target, filename=target.name, media_type="application/octet-stream")


@router.get("/api/v1/admin/config")
def admin_config(request: Request) -> dict:
    return collect_config_view(request.app)


@router.get("/api/v1/admin/stream")
async def admin_stream(
    request: Request,
    resources: str | None = None,
    session_id: str | None = None,
    interval_ms: int | None = None,
    max_events: int | None = Query(default=None, ge=1, le=1000),
) -> StreamingResponse:
    selected_resources = parse_stream_resources(resources)
    selected_interval = normalize_stream_interval(interval_ms)
    stream = iter_admin_events(
        request.app,
        request=request,
        resources=selected_resources,
        session_id=session_id,
        interval_ms=selected_interval,
        max_events=max_events,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
