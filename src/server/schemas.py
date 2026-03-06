"""Pydantic models for the HTTP wrapper API."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


class SessionState(str, Enum):
    """Session lifecycle state."""

    ACTIVE = "active"
    CLOSED = "closed"

    def __str__(self) -> str:
        return self.value


class TurnStatus(str, Enum):
    """Turn status values with type safety."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class SSEEventType(str, Enum):
    """Server-Sent Event types with type safety."""

    STATUS = "status"
    CHUNK = "chunk"
    DONE = "done"
    ERROR = "error"
    HEARTBEAT = "heartbeat"

    def __str__(self) -> str:
        return self.value


class CreateSessionRequest(BaseModel):
    """Request body for creating a new session."""

    title: str | None = None


class SessionSummaryResponse(BaseModel):
    """Compact session listing payload."""

    id: str
    title: str
    summary_text: str | None = None
    state: str
    closed_at: str | None = None
    created_at: str
    updated_at: str


class CreateSessionResponse(BaseModel):
    """Response body for a newly created session."""

    id: str
    title: str
    state: str
    closed_at: str | None = None
    created_at: str
    updated_at: str


class SessionMessageResponse(BaseModel):
    """Serialized user/assistant message."""

    seq: int
    role: str
    content: str


class SessionTurnSummaryResponse(BaseModel):
    """Compact turn summary shown inside session detail responses."""

    id: str
    status: str
    input_text: str
    created_at: str
    ended_at: str | None = None


class SessionDetailResponse(BaseModel):
    """Expanded session detail payload."""

    id: str
    title: str
    summary_text: str | None = None
    state: str
    closed_at: str | None = None
    busy: bool
    messages: list[SessionMessageResponse]
    recent_turns: list[SessionTurnSummaryResponse]
    created_at: str
    updated_at: str


class CloseSessionResponse(BaseModel):
    """Response body for deleting (closing) one session."""

    id: str
    state: str
    closed_at: str | None = None
    updated_at: str


class CreateTurnRequest(BaseModel):
    """Request body for creating one turn in a session."""

    input: str


class CreateTurnResponse(BaseModel):
    """Queued turn response payload."""

    id: str
    session_id: str
    status: str
    stream_url: str


class TurnDetailResponse(BaseModel):
    """Expanded turn payload."""

    id: str
    session_id: str
    status: str
    input_text: str
    final_output: str | None = None
    error_text: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str
    updated_at: str
