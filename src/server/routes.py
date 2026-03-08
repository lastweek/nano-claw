"""HTTP routes for session-scoped long-running agents."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from src.server.schemas import (
    CloseSessionResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DailyMemoryAppendRequest,
    DailyMemoryFileResponse,
    DailyMemoryListResponse,
    DailyMemorySummaryResponse,
    CapabilityRequestListResponse,
    CapabilityRequestResponse,
    CreateTurnRequest,
    CreateTurnResponse,
    MemoryEntryCreateRequest,
    MemoryEntryListResponse,
    MemoryEntryResponse,
    MemoryEntryUpdateRequest,
    MemoryDocumentRequest,
    MemoryDocumentResponse,
    MemorySearchHitResponse,
    MemorySearchResponse,
    MemorySettingsResponse,
    MemorySettingsUpdateRequest,
    MemoryWorkspaceResponse,
    SessionDetailResponse,
    SessionMessageResponse,
    SessionSummaryResponse,
    SessionTurnSummaryResponse,
    TurnDetailResponse,
)
from src.server.session_registry import SessionRegistry
from src.server.session_runtime import SessionBusyError, SessionClosedError
from src.database.session_database import SessionDatabase, TurnRecord


router = APIRouter()


def _get_database(request: Request) -> SessionDatabase:
    return request.app.state.database


def _get_registry(request: Request) -> SessionRegistry:
    return request.app.state.session_registry


def _get_runtime(request: Request, session_id: str):
    runtime = _get_registry(request).get_runtime(session_id)
    if runtime is None:
        raise KeyError(f"Unknown runtime for session: {session_id}")
    return runtime


def _get_memory_store(request: Request):
    return request.app.state.memory_store


def _format_sse(event_name: str, payload: dict) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


def _terminal_turn_event(turn: TurnRecord) -> tuple[str, dict]:
    if turn.status == "completed":
        return (
            "done",
            {
                "turn_id": turn.id,
                "seq": 0,
                "type": "done",
                "payload": {"final_output": turn.final_output or ""},
            },
        )
    return (
        "error",
        {
            "turn_id": turn.id,
            "seq": 0,
            "type": "error",
            "payload": {"message": turn.error_text or "Turn failed."},
        },
    )


def _status_snapshot_event(turn: TurnRecord) -> dict:
    return {
        "turn_id": turn.id,
        "seq": 0,
        "type": "status",
        "payload": {"status": turn.status},
    }


def _require_turn(request: Request, turn_id: str) -> TurnRecord:
    turn = _get_database(request).get_turn(turn_id)
    if turn is None:
        raise KeyError(f"Unknown turn: {turn_id}")
    return turn


def _require_session(request: Request, session_id: str):
    session = _get_database(request).get_session(session_id)
    if session is None:
        raise KeyError(f"Unknown session: {session_id}")
    return session


def _require_memory_enabled(request: Request):
    memory_store = _get_memory_store(request)
    if not memory_store.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session memory is disabled.",
        )
    return memory_store


def _serialize_memory_entry(session_id: str, entry) -> MemoryEntryResponse:
    return MemoryEntryResponse(
        id=entry.entry_id,
        session_id=session_id,
        kind=entry.kind,
        title=entry.title,
        content=entry.content,
        source=entry.source,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        confidence=entry.confidence,
        last_verified_at=entry.last_verified_at,
        status=entry.status,
        supersedes=entry.supersedes,
    )


def _serialize_memory_settings(session_id: str, memory_store) -> MemorySettingsResponse:
    settings = memory_store.get_settings(session_id)
    return MemorySettingsResponse(
        session_id=session_id,
        mode=settings.mode,
        read_policy=settings.read_policy,
        prompt_policy=settings.prompt_policy,
        auto_retrieve_enabled=settings.auto_retrieve_enabled,
        manual_write_enabled=settings.manual_write_enabled,
        autonomous_write_enabled=settings.autonomous_write_enabled,
        debug_enabled=memory_store.debug_enabled(),
        path=str(memory_store.settings_path(session_id)),
    )


@router.get("/", include_in_schema=False)
def root(request: Request):
    """Serve static UI from this process when enabled."""
    if not request.app.state.runtime_config.server.serve_ui:
        return {"status": "ok", "ui": "disabled"}
    return FileResponse(Path(request.app.state.static_dir) / "index.html")


@router.get("/api/v1/health")
def health() -> dict[str, str]:
    """Basic liveness probe."""
    return {"status": "ok"}


@router.get("/api/v1/sessions", response_model=list[SessionSummaryResponse])
def list_sessions(request: Request) -> list[SessionSummaryResponse]:
    """List persisted sessions newest first."""
    return [
        SessionSummaryResponse(**session.__dict__)
        for session in _get_database(request).list_sessions()
    ]


@router.post(
    "/api/v1/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_session(request: Request, payload: CreateSessionRequest) -> CreateSessionResponse:
    """Create a persisted session and start its long-running runtime."""
    database = _get_database(request)
    registry = _get_registry(request)
    session = database.create_session(payload.title)
    try:
        registry.ensure_runtime(session.id)
    except Exception as exc:
        try:
            database.delete_session(session.id)
        except Exception:
            pass
        try:
            _get_memory_store(request).delete_session_memory(session.id)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize session runtime: {exc}",
        ) from exc
    return CreateSessionResponse(
        id=session.id,
        title=session.title,
        state=session.state,
        closed_at=session.closed_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get("/api/v1/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(request: Request, session_id: str) -> SessionDetailResponse:
    """Return persisted transcript and current runtime state for one session."""
    database = _get_database(request)
    registry = _get_registry(request)
    session_detail = database.get_session_detail(session_id)
    if session_detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown session: {session_id}")

    runtime = registry.get_runtime(session_id)
    busy = bool(runtime and not runtime.is_closed() and runtime.is_busy())

    return SessionDetailResponse(
        id=session_detail.session.id,
        title=session_detail.session.title,
        summary_text=session_detail.session.summary_text,
        state=session_detail.session.state,
        closed_at=session_detail.session.closed_at,
        busy=busy,
        created_at=session_detail.session.created_at,
        updated_at=session_detail.session.updated_at,
        messages=[
            SessionMessageResponse(
                seq=message.seq,
                role=message.role,
                content=message.content,
            )
            for message in session_detail.messages
        ],
        recent_turns=[
            SessionTurnSummaryResponse(
                id=turn.id,
                status=turn.status,
                input_text=turn.input_text,
                created_at=turn.created_at,
                ended_at=turn.ended_at,
            )
            for turn in session_detail.recent_turns
        ],
    )


@router.post("/api/v1/sessions/{session_id}/runtime/reload")
def reload_session_runtime(request: Request, session_id: str) -> dict:
    """Reload config-backed tools and skills for one idle session runtime."""
    _require_session(request, session_id)
    try:
        runtime = _get_registry(request).ensure_runtime(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        runtime._config_loader = request.app.state.runtime_config_loader
        payload = runtime.reload_capabilities()
    except SessionBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session already has an active turn.",
        ) from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    request.app.state.runtime_config = runtime.runtime_config
    request.app.state.session_registry.update_runtime_config(runtime.runtime_config)
    request.app.state.memory_store.runtime_config = runtime.runtime_config
    request.app.state.extension_manager = request.app.state.extension_manager.__class__(
        repo_root=request.app.state.repo_root,
        runtime_config=runtime.runtime_config,
    )
    request.app.state.extension_manager.discover()
    return payload


@router.get(
    "/api/v1/sessions/{session_id}/capability-requests",
    response_model=CapabilityRequestListResponse,
)
def list_capability_requests(request: Request, session_id: str) -> CapabilityRequestListResponse:
    """Return runtime-scoped capability requests for one session."""
    _require_session(request, session_id)
    try:
        runtime = _get_registry(request).ensure_runtime(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return CapabilityRequestListResponse(
        session_id=session_id,
        requests=[CapabilityRequestResponse(**item) for item in runtime.list_capability_requests()],
    )


@router.post(
    "/api/v1/sessions/{session_id}/capability-requests/{request_id}/dismiss",
    response_model=CapabilityRequestResponse,
)
def dismiss_capability_request(
    request: Request,
    session_id: str,
    request_id: str,
) -> CapabilityRequestResponse:
    """Dismiss one pending capability request."""
    _require_session(request, session_id)
    try:
        runtime = _get_registry(request).ensure_runtime(session_id)
        payload = runtime.dismiss_capability_request(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown capability request: {request_id}") from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return CapabilityRequestResponse(**payload)


@router.post(
    "/api/v1/sessions/{session_id}/capability-requests/{request_id}/resolve",
    response_model=CapabilityRequestResponse,
)
def resolve_capability_request(
    request: Request,
    session_id: str,
    request_id: str,
) -> CapabilityRequestResponse:
    """Resolve one capability request manually."""
    _require_session(request, session_id)
    try:
        runtime = _get_registry(request).ensure_runtime(session_id)
        payload = runtime.resolve_capability_request(request_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown capability request: {request_id}") from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return CapabilityRequestResponse(**payload)


@router.delete("/api/v1/sessions/{session_id}", response_model=CloseSessionResponse)
def delete_session(request: Request, session_id: str) -> CloseSessionResponse:
    """Close a session and release its runtime resources."""
    database = _get_database(request)
    registry = _get_registry(request)
    memory_store = _get_memory_store(request)
    session = database.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown session: {session_id}")

    closed = database.close_session(session_id)
    registry.close_runtime(session_id)
    memory_store.delete_session_memory(session_id)
    return CloseSessionResponse(
        id=closed.id,
        state=closed.state,
        closed_at=closed.closed_at,
        updated_at=closed.updated_at,
    )


@router.post(
    "/api/v1/sessions/{session_id}/turns",
    response_model=CreateTurnResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_turn(request: Request, session_id: str, payload: CreateTurnRequest) -> CreateTurnResponse:
    """Create one queued turn for the target session runtime."""
    normalized_input = payload.input.strip()
    if not normalized_input:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Input must not be empty.")
    if normalized_input.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slash commands are CLI-only in HTTP v1.",
        )

    registry = _get_registry(request)

    try:
        runtime = registry.ensure_runtime(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is closed.") from exc

    try:
        turn = runtime.submit_turn(payload.input)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SessionBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session already has an active turn.",
        ) from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is closed.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return CreateTurnResponse(
        id=turn.id,
        session_id=turn.session_id,
        status=turn.status,
        stream_url=f"/api/v1/turns/{turn.id}/stream",
    )


@router.get("/api/v1/turns/{turn_id}", response_model=TurnDetailResponse)
def get_turn(request: Request, turn_id: str) -> TurnDetailResponse:
    """Return one turn detail payload."""
    try:
        turn = _require_turn(request, turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TurnDetailResponse(**turn.__dict__)


@router.get(
    "/api/v1/sessions/{session_id}/memory",
    response_model=MemoryWorkspaceResponse,
)
def get_session_memory_workspace(request: Request, session_id: str) -> MemoryWorkspaceResponse:
    """Return one session's memory workspace summary."""
    memory_store = _require_memory_enabled(request)
    try:
        _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    summary = memory_store.describe_workspace(session_id)
    return MemoryWorkspaceResponse(
        session_id=session_id,
        root_dir=summary["root_dir"],
        document_path=summary["document_path"],
        settings_path=summary["settings_path"],
        audit_path=summary["audit_path"],
        entry_count=summary["entry_count"],
        daily_files=list(summary["daily_files"]),
        settings_mode=summary["settings"]["mode"],
        settings_read_policy=summary["settings"]["read_policy"],
        settings_prompt_policy=summary["settings"]["prompt_policy"],
        debug_enabled=bool(summary.get("debug_enabled")),
    )


@router.get(
    "/api/v1/sessions/{session_id}/memory/document",
    response_model=MemoryDocumentResponse,
)
def get_session_memory_document(request: Request, session_id: str) -> MemoryDocumentResponse:
    """Return the raw curated memory document for one session."""
    memory_store = _require_memory_enabled(request)
    try:
        _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    path = memory_store.curated_document_path(session_id)
    content = memory_store.read_curated_document(session_id)
    return MemoryDocumentResponse(
        session_id=session_id,
        path=str(path),
        content=content,
    )


@router.put(
    "/api/v1/sessions/{session_id}/memory/document",
    response_model=MemoryDocumentResponse,
)
def put_session_memory_document(
    request: Request,
    session_id: str,
    payload: MemoryDocumentRequest,
) -> MemoryDocumentResponse:
    """Replace the raw curated memory document for one active session."""
    memory_store = _require_memory_enabled(request)
    try:
        session = _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if session.state != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is closed.")

    path = memory_store.write_curated_document(session_id, payload.content)
    return MemoryDocumentResponse(
        session_id=session_id,
        path=str(path),
        content=memory_store.read_curated_document(session_id),
    )


@router.get(
    "/api/v1/sessions/{session_id}/memory/entries",
    response_model=MemoryEntryListResponse,
)
def list_session_memory_entries(
    request: Request,
    session_id: str,
    kind: str | None = None,
    entry_status: str | None = Query(default=None, alias="status"),
    q: str | None = None,
    include_inactive: bool = True,
) -> MemoryEntryListResponse:
    """List structured curated memory entries for one session."""
    memory_store = _require_memory_enabled(request)
    try:
        _require_session(request, session_id)
        entries = memory_store.list_entries(
            session_id,
            kind=kind,
            status=entry_status,
            query=q,
            include_inactive=include_inactive,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MemoryEntryListResponse(
        session_id=session_id,
        entries=[_serialize_memory_entry(session_id, entry) for entry in entries],
    )


@router.post(
    "/api/v1/sessions/{session_id}/memory/entries",
    response_model=MemoryEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_session_memory_entry(
    request: Request,
    session_id: str,
    payload: MemoryEntryCreateRequest,
) -> MemoryEntryResponse:
    """Create or upsert one curated memory entry."""
    memory_store = _require_memory_enabled(request)
    try:
        session = _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if session.state != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is closed.")

    try:
        entry = memory_store.upsert_curated_entry(
            session_id,
            kind=payload.kind,
            title=payload.title,
            content=payload.content,
            reason=payload.reason,
            source=payload.source or "http_api",
            confidence=payload.confidence,
            last_verified_at=payload.last_verified_at,
            actor="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _serialize_memory_entry(session_id, entry)


@router.patch(
    "/api/v1/sessions/{session_id}/memory/entries/{entry_id}",
    response_model=MemoryEntryResponse,
)
def patch_session_memory_entry(
    request: Request,
    session_id: str,
    entry_id: str,
    payload: MemoryEntryUpdateRequest,
) -> MemoryEntryResponse:
    """Update lifecycle or content for one curated memory entry."""
    memory_store = _require_memory_enabled(request)
    try:
        session = _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if session.state != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is closed.")

    try:
        if payload.action == "update":
            entry = memory_store.update_curated_entry(
                session_id,
                entry_id,
                title=payload.title,
                content=payload.content,
                confidence=payload.confidence,
                source=payload.source or "http_api",
                last_verified_at=payload.last_verified_at,
                reason=payload.reason,
                actor="manual",
            )
        elif payload.action == "archive":
            entry = memory_store.archive_curated_entry(
                session_id,
                entry_id,
                reason=payload.reason,
                actor="manual",
            )
        elif payload.action == "supersede":
            if payload.content is None:
                raise ValueError("content is required for supersede")
            entry = memory_store.supersede_curated_entry(
                session_id,
                entry_id,
                title=payload.title,
                content=payload.content,
                reason=payload.reason,
                source=payload.source or "http_api",
                confidence=payload.confidence,
                last_verified_at=payload.last_verified_at,
                actor="manual",
            )
        else:
            raise ValueError("action must be one of: update, archive, supersede")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _serialize_memory_entry(session_id, entry)


@router.delete("/api/v1/sessions/{session_id}/memory/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session_memory_entry(request: Request, session_id: str, entry_id: str) -> None:
    """Delete one curated memory entry."""
    memory_store = _require_memory_enabled(request)
    try:
        session = _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if session.state != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is closed.")

    try:
        memory_store.delete_curated_entry(
            session_id,
            entry_id=entry_id,
            reason="http_api delete",
            actor="manual",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/api/v1/sessions/{session_id}/memory/settings",
    response_model=MemorySettingsResponse,
)
def get_session_memory_settings(request: Request, session_id: str) -> MemorySettingsResponse:
    """Return one session's memory mode settings."""
    memory_store = _require_memory_enabled(request)
    try:
        _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _serialize_memory_settings(session_id, memory_store)


@router.patch(
    "/api/v1/sessions/{session_id}/memory/settings",
    response_model=MemorySettingsResponse,
)
def patch_session_memory_settings(
    request: Request,
    session_id: str,
    payload: MemorySettingsUpdateRequest,
) -> MemorySettingsResponse:
    """Update the current session memory mode."""
    memory_store = _require_memory_enabled(request)
    try:
        session = _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if session.state != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is closed.")
    try:
        memory_store.update_settings(
            session_id,
            mode=payload.mode,
            read_policy=payload.read_policy,
            prompt_policy=payload.prompt_policy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _serialize_memory_settings(session_id, memory_store)


@router.get(
    "/api/v1/sessions/{session_id}/memory/daily",
    response_model=DailyMemoryListResponse,
)
def get_session_memory_daily_logs(request: Request, session_id: str) -> DailyMemoryListResponse:
    """List daily memory files for one session."""
    memory_store = _require_memory_enabled(request)
    try:
        _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    files = [
        DailyMemorySummaryResponse(
            date=date,
            path=str(memory_store.daily_log_path(session_id, date)),
        )
        for date in memory_store.list_daily_logs(session_id)
    ]
    return DailyMemoryListResponse(session_id=session_id, files=files)


@router.get(
    "/api/v1/sessions/{session_id}/memory/daily/{date}",
    response_model=DailyMemoryFileResponse,
)
def get_session_memory_daily_log(
    request: Request,
    session_id: str,
    date: str,
) -> DailyMemoryFileResponse:
    """Return one daily memory file."""
    memory_store = _require_memory_enabled(request)
    try:
        _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        path = memory_store.daily_log_path(session_id, date)
        content = memory_store.read_daily_log(session_id, date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return DailyMemoryFileResponse(
        session_id=session_id,
        date=date,
        path=str(path),
        content=content,
    )


@router.post(
    "/api/v1/sessions/{session_id}/memory/daily/{date}",
    response_model=DailyMemoryFileResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_session_memory_daily_log(
    request: Request,
    session_id: str,
    date: str,
    payload: DailyMemoryAppendRequest,
) -> DailyMemoryFileResponse:
    """Append one entry to the selected daily memory file."""
    memory_store = _require_memory_enabled(request)
    try:
        session = _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if session.state != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Session is closed.")

    try:
        path = memory_store.append_daily_log(
            session_id,
            title=payload.title,
            content=payload.content,
            date=date,
        )
        content = memory_store.read_daily_log(session_id, date)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return DailyMemoryFileResponse(
        session_id=session_id,
        date=date,
        path=str(path),
        content=content,
    )


@router.get(
    "/api/v1/sessions/{session_id}/memory/search",
    response_model=MemorySearchResponse,
)
def search_session_memory(
    request: Request,
    session_id: str,
    q: str,
    limit: int | None = None,
    include_daily: bool = True,
    include_inactive: bool = False,
) -> MemorySearchResponse:
    """Search curated memory and optional daily logs for one session."""
    memory_store = _require_memory_enabled(request)
    try:
        _require_session(request, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        hits = memory_store.search(
            session_id,
            query=q,
            limit=limit,
            include_daily=include_daily,
            include_inactive=include_inactive,
            actor="http_api",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return MemorySearchResponse(
        session_id=session_id,
        query=q,
        hits=[MemorySearchHitResponse(**hit.__dict__) for hit in hits],
    )


@router.get("/api/v1/turns/{turn_id}/stream")
def stream_turn(request: Request, turn_id: str) -> StreamingResponse:
    """Stream one turn over SSE as live-tail output."""
    try:
        turn = _require_turn(request, turn_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if turn.status in {"completed", "failed"}:
        event_name, payload = _terminal_turn_event(turn)

        def terminal_stream():
            yield _format_sse(event_name, payload)

        return StreamingResponse(
            terminal_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    live_events = request.app.state.event_bus.subscribe(
        turn_id,
        heartbeat_seconds=request.app.state.runtime_config.server.sse_heartbeat_seconds,
    )

    def event_stream():
        # Terminal turns are served from the DB immediately so reconnects do not depend on
        # the in-memory event bus retaining historical chunk data.
        yield _format_sse("status", _status_snapshot_event(turn))
        for live_event in live_events:
            yield _format_sse(live_event.event, live_event.data)
            if live_event.event in {"done", "error"}:
                return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
