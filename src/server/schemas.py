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


class MemoryWorkspaceResponse(BaseModel):
    """Summary of one session's file-backed memory workspace."""

    session_id: str
    root_dir: str
    document_path: str
    settings_path: str
    audit_path: str
    entry_count: int
    daily_files: list[str]
    settings_mode: str


class MemoryDocumentRequest(BaseModel):
    """Raw MEMORY.md update payload."""

    content: str


class MemoryDocumentResponse(BaseModel):
    """Serialized MEMORY.md document."""

    session_id: str
    path: str
    content: str


class DailyMemorySummaryResponse(BaseModel):
    """One available daily memory file."""

    date: str
    path: str


class DailyMemoryListResponse(BaseModel):
    """List of available daily memory files for one session."""

    session_id: str
    files: list[DailyMemorySummaryResponse]


class DailyMemoryAppendRequest(BaseModel):
    """Append one timestamped entry to a daily memory file."""

    title: str
    content: str


class DailyMemoryFileResponse(BaseModel):
    """Serialized daily memory document."""

    session_id: str
    date: str
    path: str
    content: str


class MemorySearchHitResponse(BaseModel):
    """One file-backed memory search hit."""

    scope: str
    path: str
    title: str
    snippet: str
    entry_id: str | None = None
    kind: str | None = None
    status: str | None = None
    confidence: float | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_verified_at: str | None = None
    date: str | None = None


class MemorySearchResponse(BaseModel):
    """Search response for one session memory workspace."""

    session_id: str
    query: str
    hits: list[MemorySearchHitResponse]


class MemoryEntryResponse(BaseModel):
    """One structured curated memory entry."""

    id: str
    session_id: str
    kind: str
    title: str
    content: str
    source: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    confidence: float | None = None
    last_verified_at: str | None = None
    status: str
    supersedes: str | None = None


class MemoryEntryCreateRequest(BaseModel):
    """Create or upsert one curated memory entry."""

    kind: str
    title: str
    content: str
    reason: str
    source: str | None = None
    confidence: float | None = None
    last_verified_at: str | None = None


class MemoryEntryUpdateRequest(BaseModel):
    """Structured curated memory mutation request."""

    action: str
    title: str | None = None
    content: str | None = None
    reason: str
    source: str | None = None
    confidence: float | None = None
    last_verified_at: str | None = None


class MemoryEntryListResponse(BaseModel):
    """List of structured curated memory entries."""

    session_id: str
    entries: list[MemoryEntryResponse]


class MemorySettingsResponse(BaseModel):
    """Per-session memory mode settings."""

    session_id: str
    mode: str
    auto_retrieve_enabled: bool
    manual_write_enabled: bool
    autonomous_write_enabled: bool
    path: str


class MemorySettingsUpdateRequest(BaseModel):
    """Update per-session memory mode settings."""

    mode: str


class MemoryAuditEventResponse(BaseModel):
    """One session memory audit event."""

    timestamp: str
    session_id: str
    event: str
    payload: dict


class MemoryAuditResponse(BaseModel):
    """List of recent session memory audit events."""

    session_id: str
    events: list[MemoryAuditEventResponse]
