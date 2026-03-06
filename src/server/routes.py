"""HTTP routes for session-scoped long-running agents."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from src.server.schemas import (
    CloseSessionResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    CreateTurnRequest,
    CreateTurnResponse,
    SessionDetailResponse,
    SessionMessageResponse,
    SessionSummaryResponse,
    SessionTurnSummaryResponse,
    TurnDetailResponse,
)
from src.server.session_registry import SessionRegistry
from src.server.session_runtime import SessionBusyError, SessionClosedError
from src.store.repository import AppStore, TurnRecord


router = APIRouter()


def _get_store(request: Request) -> AppStore:
    return request.app.state.store


def _get_registry(request: Request) -> SessionRegistry:
    return request.app.state.session_registry


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
    turn = _get_store(request).get_turn(turn_id)
    if turn is None:
        raise KeyError(f"Unknown turn: {turn_id}")
    return turn


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
        for session in _get_store(request).list_sessions()
    ]


@router.post(
    "/api/v1/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_session(request: Request, payload: CreateSessionRequest) -> CreateSessionResponse:
    """Create a persisted session and start its long-running runtime."""
    store = _get_store(request)
    registry = _get_registry(request)
    session = store.create_session(payload.title)
    try:
        registry.ensure_runtime(session.id)
    except Exception as exc:
        try:
            store.delete_session_record(session.id)
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
    store = _get_store(request)
    registry = _get_registry(request)
    session_detail = store.get_session_detail(session_id)
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


@router.delete("/api/v1/sessions/{session_id}", response_model=CloseSessionResponse)
def delete_session(request: Request, session_id: str) -> CloseSessionResponse:
    """Close a session and release its runtime resources."""
    store = _get_store(request)
    registry = _get_registry(request)
    session = store.get_session_record(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown session: {session_id}")

    closed = store.close_session(session_id)
    registry.close_runtime(session_id)
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
