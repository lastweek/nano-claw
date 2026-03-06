"""Typed models for managed Markdown session memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MemoryKind = Literal["fact", "decision", "task", "note"]
MemoryStatus = Literal["active", "archived", "superseded"]
MemoryMode = Literal["off", "manual_only", "auto"]


@dataclass(frozen=True)
class MemorySettings:
    """Per-session memory behavior flags stored outside the Markdown document."""

    mode: MemoryMode = "manual_only"

    @property
    def auto_retrieve_enabled(self) -> bool:
        return self.mode != "off"

    @property
    def manual_write_enabled(self) -> bool:
        return self.mode in {"manual_only", "auto"}

    @property
    def autonomous_write_enabled(self) -> bool:
        return self.mode == "auto"


@dataclass(frozen=True)
class CuratedMemoryEntry:
    """One structured entry parsed from `MEMORY.md`."""

    entry_id: str
    kind: MemoryKind
    title: str
    content: str
    source: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    confidence: float | None = None
    last_verified_at: str | None = None
    status: MemoryStatus = "active"
    supersedes: str | None = None


@dataclass(frozen=True)
class DailyMemoryEntry:
    """One append-only daily log entry."""

    date: str
    heading: str
    content: str
    path: str


@dataclass(frozen=True)
class MemorySearchHit:
    """One search hit from curated memory or daily logs."""

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
    score: float = 0.0


@dataclass(frozen=True)
class MemoryPromptSelection:
    """Bounded memory entries selected for automatic prompt injection."""

    note: str
    entries: list[CuratedMemoryEntry]


@dataclass(frozen=True)
class MemoryCandidate:
    """Candidate long-term memory extracted from a turn outcome."""

    kind: MemoryKind
    title: str
    content: str
    reason: str
    source: str
    confidence: float | None = None
    last_verified_at: str | None = None


__all__ = [
    "CuratedMemoryEntry",
    "DailyMemoryEntry",
    "MemoryCandidate",
    "MemoryKind",
    "MemoryMode",
    "MemoryPromptSelection",
    "MemorySearchHit",
    "MemorySettings",
    "MemoryStatus",
]
