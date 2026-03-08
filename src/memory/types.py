"""Typed data structures for managed Markdown session memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args


MemoryKind = Literal["fact", "decision", "task", "note"]
MemoryStatus = Literal["active", "archived", "superseded"]
MemoryMode = Literal["off", "manual_only", "auto"]
MemoryReadPolicyName = Literal["curated_only", "curated_plus_recent_daily", "search_all_ranked"]
MemoryPromptPolicyName = Literal["curated_only", "curated_plus_recent_daily", "search_all_ranked"]
MemoryPromptItemScope = Literal["curated", "daily"]
VALID_MEMORY_MODES = set(get_args(MemoryMode))
VALID_MEMORY_READ_POLICIES = set(get_args(MemoryReadPolicyName))
VALID_MEMORY_PROMPT_POLICIES = set(get_args(MemoryPromptPolicyName))


@dataclass(frozen=True)
class MemorySettings:
    """Per-session memory behavior flags stored outside the Markdown document."""

    mode: MemoryMode = "manual_only"
    read_policy: MemoryReadPolicyName = "curated_plus_recent_daily"
    prompt_policy: MemoryPromptPolicyName = "curated_plus_recent_daily"

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
class MemoryPromptItem:
    """One memory item selected for prompt injection."""

    scope: MemoryPromptItemScope
    title: str
    content: str
    path: str
    entry_id: str | None = None
    kind: str | None = None
    date: str | None = None
    score: float = 0.0
    updated_at: str | None = None
    last_verified_at: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class MemoryPromptSelection:
    """Bounded memory entries selected for automatic prompt injection."""

    policy_name: str
    note: str
    items: list[MemoryPromptItem]


@dataclass(frozen=True)
class MemorySearchPlan:
    """Resolved search behavior for one memory_search call."""

    policy_name: str
    query: str
    limit: int | None
    include_daily: bool
    include_inactive: bool
    recent_daily_days: int | None = None


@dataclass(frozen=True)
class MemoryWriteCandidate:
    """Candidate durable memory extracted before persistence."""

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
    "MemoryPromptItem",
    "MemoryWriteCandidate",
    "MemoryKind",
    "MemoryMode",
    "MemoryPromptItemScope",
    "MemoryPromptPolicyName",
    "MemoryPromptSelection",
    "MemoryReadPolicyName",
    "MemorySearchPlan",
    "MemorySearchHit",
    "MemorySettings",
    "MemoryStatus",
    "VALID_MEMORY_MODES",
    "VALID_MEMORY_PROMPT_POLICIES",
    "VALID_MEMORY_READ_POLICIES",
]
