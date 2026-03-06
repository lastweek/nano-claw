"""Managed Markdown session memory without cache or SQL indexing."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
import re
import shutil
from typing import Any
from uuid import uuid4

from src.config import Config, config
from src.memory.models import (
    CuratedMemoryEntry,
    DailyMemoryEntry,
    MemoryCandidate,
    MemoryPromptSelection,
    MemorySearchHit,
    MemorySettings,
)
from src.memory.policy import evaluate_autonomous_write, evaluate_manual_write
from src.session_paths import DEFAULT_SESSIONS_ROOT, resolve_session_dir, resolve_sessions_root
from src.utils import resolve_path

DEFAULT_MEMORY_ROOT = DEFAULT_SESSIONS_ROOT
LEGACY_MEMORY_ROOT = ".nano-claw/memory"
_SECTION_HEADINGS = {
    "fact": "Facts",
    "decision": "Decisions",
    "task": "Tasks",
    "note": "Notes",
}
_HEADING_TO_KIND = {value: key for key, value in _SECTION_HEADINGS.items()}
_ENTRY_HEADING_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$")
_METADATA_LINE_RE = re.compile(r"^- (?P<key>[a-z_]+):\s*(?P<value>.*)$")
_AUTO_CANDIDATE_RE = re.compile(
    r"^(?:memory\s+)?(?P<kind>fact|decision|task|note)\s*:\s*(?P<title>[^:]+?)\s*::\s*(?P<content>.+)$",
    re.IGNORECASE,
)
_VALID_ENTRY_METADATA_KEYS = {
    "entry_id",
    "kind",
    "title",
    "source",
    "created_at",
    "updated_at",
    "confidence",
    "last_verified_at",
    "status",
    "supersedes",
}
_VALID_MEMORY_MODES = {"off", "manual_only", "auto"}
_VALID_MEMORY_STATUSES = {"active", "archived", "superseded"}
_AUTO_RETRIEVAL_LIMIT = 3


def migrate_legacy_memory_root(memory_root: str | Path, repo_root: Path) -> str | None:
    """Move repo-local memory files into the default global memory root when safe."""
    target_memory_root = resolve_path(memory_root, repo_root)
    default_global_memory_root = resolve_path(DEFAULT_MEMORY_ROOT, repo_root)
    if target_memory_root != default_global_memory_root:
        return None

    legacy_memory_root = resolve_path(LEGACY_MEMORY_ROOT, repo_root)
    if not legacy_memory_root.exists() or not any(legacy_memory_root.iterdir()):
        return None

    if target_memory_root.exists() and any(target_memory_root.iterdir()):
        return (
            "Legacy repo-local memory was left untouched at "
            f"{legacy_memory_root} because global memory already exists at {target_memory_root}."
        )

    target_memory_root.parent.mkdir(parents=True, exist_ok=True)
    if target_memory_root.exists() and not any(target_memory_root.iterdir()):
        target_memory_root.rmdir()
    shutil.move(str(legacy_memory_root), str(target_memory_root))
    return f"Migrated legacy repo-local memory from {legacy_memory_root} to {target_memory_root}."


class SessionMemoryStore:
    """Manage per-session Markdown memory files inside the shared sessions root."""

    def __init__(
        self,
        *,
        repo_root: Path,
        runtime_config: Config | None = None,
        session_lookup=None,
    ) -> None:
        self.runtime_config = runtime_config or config
        self.repo_root = resolve_path(repo_root)
        self.sessions_root = resolve_sessions_root(self.runtime_config.logging.log_dir, self.repo_root)
        self.root_dir = self.sessions_root
        self._session_lookup = session_lookup
        self._lock = RLock()

    def is_enabled(self) -> bool:
        return bool(self.runtime_config.memory.enabled)

    def session_root(self, session_id: str) -> Path:
        session = self._lookup_session(session_id)
        title = getattr(session, "title", None) if session is not None else None
        created_at = getattr(session, "created_at", None) if session is not None else None
        return resolve_session_dir(
            self.sessions_root,
            session_id,
            title=title,
            created_at=created_at,
        )

    def curated_document_path(self, session_id: str) -> Path:
        return self.session_root(session_id) / "MEMORY.md"

    def daily_root(self, session_id: str) -> Path:
        return self.session_root(session_id) / "daily"

    def settings_path(self, session_id: str) -> Path:
        return self.session_root(session_id) / "memory-settings.json"

    def audit_path(self, session_id: str) -> Path:
        return self.session_root(session_id) / "memory-audit.jsonl"

    def ensure_curated_document(self, session_id: str) -> Path:
        path = self.curated_document_path(session_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(self._render_curated_document([]), encoding="utf-8")
        return path

    def delete_session_memory(self, session_id: str) -> None:
        target = self.session_root(session_id)
        if not target.exists():
            return
        with self._lock:
            shutil.rmtree(target, ignore_errors=True)

    def get_settings(self, session_id: str) -> MemorySettings:
        path = self.settings_path(session_id)
        if not path.exists():
            return MemorySettings()
        payload = json.loads(path.read_text(encoding="utf-8"))
        mode = str(payload.get("mode", "manual_only")).strip().lower()
        if mode not in _VALID_MEMORY_MODES:
            raise ValueError(f"Unsupported memory mode in {path}: {mode}")
        return MemorySettings(mode=mode)

    def update_settings(self, session_id: str, *, mode: str) -> MemorySettings:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in _VALID_MEMORY_MODES:
            raise ValueError("mode must be one of: off, manual_only, auto")
        settings = MemorySettings(mode=normalized_mode)
        path = self.settings_path(session_id)
        payload = {
            "mode": settings.mode,
            "updated_at": self._now_iso(),
        }
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._append_audit_event(
            session_id,
            "settings_updated",
            mode=settings.mode,
        )
        return settings

    def read_curated_document(self, session_id: str) -> str:
        path = self.ensure_curated_document(session_id)
        return path.read_text(encoding="utf-8")

    def write_curated_document(self, session_id: str, content: str, *, reason: str = "manual document update") -> Path:
        self._assert_write_allowed(session_id, actor="manual")
        entries = self._parse_curated_entries(content)
        normalized_entries = [
            self._normalize_entry(
                entry,
                default_source="manual_document",
                preserve_timestamps=True,
            )
            for entry in entries
        ]
        for entry in normalized_entries:
            decision = evaluate_manual_write(
                MemoryCandidate(
                    kind=entry.kind,
                    title=entry.title,
                    content=entry.content,
                    reason=reason,
                    source=entry.source or "manual_document",
                    confidence=entry.confidence,
                    last_verified_at=entry.last_verified_at,
                ),
                self.get_settings(session_id),
            )
            if not decision.accepted:
                raise ValueError(decision.reason)
        path = self.ensure_curated_document(session_id)
        rendered = self._render_curated_document(normalized_entries)
        with self._lock:
            path.write_text(rendered, encoding="utf-8")
        self._append_audit_event(
            session_id,
            "document_replaced",
            reason=reason,
            entry_ids=[entry.entry_id for entry in normalized_entries],
        )
        return path

    def list_entries(
        self,
        session_id: str,
        *,
        kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
        include_inactive: bool = True,
    ) -> list[CuratedMemoryEntry]:
        entries = self._load_entries(session_id)
        if kind is not None:
            normalized_kind = self._normalize_kind(kind)
            entries = [entry for entry in entries if entry.kind == normalized_kind]
        if status is not None:
            normalized_status = self._normalize_status(status)
            entries = [entry for entry in entries if entry.status == normalized_status]
        elif not include_inactive:
            entries = [entry for entry in entries if entry.status == "active"]
        if query:
            normalized_query = self._normalize_query(query)
            scored = [
                (self._score_curated_entry(entry, normalized_query), entry)
                for entry in entries
            ]
            entries = [
                entry
                for score, entry in sorted(
                    scored,
                    key=lambda item: (
                        -item[0],
                        item[1].updated_at or "",
                        item[1].entry_id,
                    ),
                )
                if score > 0
            ]
        return entries

    def get_entry(self, session_id: str, entry_id: str) -> CuratedMemoryEntry | None:
        for entry in self._load_entries(session_id):
            if entry.entry_id == entry_id:
                return entry
        return None

    def read_curated_section(self, session_id: str, kind: str, *, include_inactive: bool = True) -> str:
        normalized_kind = self._normalize_kind(kind)
        heading = _SECTION_HEADINGS[normalized_kind]
        entries = self.list_entries(
            session_id,
            kind=normalized_kind,
            include_inactive=include_inactive,
        )
        lines = [f"## {heading}", ""]
        if not entries:
            lines.append("_No entries_")
        else:
            for entry in entries:
                lines.extend(self._render_single_entry_lines(entry))
        return "\n".join(lines).rstrip() + "\n"

    def read_entry(self, session_id: str, entry_id: str) -> CuratedMemoryEntry:
        entry = self.get_entry(session_id, entry_id)
        if entry is None:
            raise FileNotFoundError(f"Unknown memory entry: {entry_id}")
        return entry

    def upsert_curated_entry(
        self,
        session_id: str,
        *,
        kind: str,
        title: str,
        content: str,
        reason: str,
        source: str = "manual",
        confidence: float | None = None,
        last_verified_at: str | None = None,
        actor: str = "manual",
    ) -> CuratedMemoryEntry:
        normalized_kind = self._normalize_kind(kind)
        normalized_title = title.strip()
        normalized_content = content.strip()
        if not normalized_title:
            raise ValueError("title is required")
        if not normalized_content:
            raise ValueError("content is required")
        self._assert_write_allowed(session_id, actor=actor)

        candidate = MemoryCandidate(
            kind=normalized_kind,
            title=normalized_title,
            content=normalized_content,
            reason=reason,
            source=source,
            confidence=confidence,
            last_verified_at=last_verified_at,
        )
        decision = self._evaluate_write_candidate(session_id, candidate, actor=actor)
        if not decision.accepted:
            self._append_audit_event(
                session_id,
                "write_rejected",
                actor=actor,
                action="upsert_curated",
                kind=normalized_kind,
                title=normalized_title,
                reason=reason,
                rejection_reason=decision.reason,
            )
            raise ValueError(decision.reason)

        entries = self._load_entries(session_id)
        now = self._now_iso()
        existing_index = next(
            (
                index
                for index, entry in enumerate(entries)
                if entry.kind == normalized_kind and entry.title == normalized_title
            ),
            None,
        )
        if existing_index is not None:
            existing = entries[existing_index]
            updated_entry = CuratedMemoryEntry(
                entry_id=existing.entry_id,
                kind=normalized_kind,
                title=normalized_title,
                content=normalized_content,
                source=source,
                created_at=existing.created_at or now,
                updated_at=now,
                confidence=self._normalize_confidence(confidence if confidence is not None else existing.confidence),
                last_verified_at=last_verified_at if last_verified_at is not None else existing.last_verified_at,
                status="active",
                supersedes=existing.supersedes,
            )
            entries[existing_index] = updated_entry
            outcome = "updated"
        else:
            updated_entry = CuratedMemoryEntry(
                entry_id=self._new_entry_id(),
                kind=normalized_kind,
                title=normalized_title,
                content=normalized_content,
                source=source,
                created_at=now,
                updated_at=now,
                confidence=self._normalize_confidence(confidence),
                last_verified_at=last_verified_at,
                status="active",
                supersedes=None,
            )
            entries.append(updated_entry)
            outcome = "created"

        self._write_entries(session_id, entries)
        self._append_audit_event(
            session_id,
            "write_accepted",
            actor=actor,
            action="upsert_curated",
            outcome=outcome,
            entry_id=updated_entry.entry_id,
            kind=updated_entry.kind,
            title=updated_entry.title,
            reason=reason,
            source=source,
            confidence=updated_entry.confidence,
        )
        return updated_entry

    def update_curated_entry(
        self,
        session_id: str,
        entry_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        confidence: float | None = None,
        source: str | None = None,
        last_verified_at: str | None = None,
        reason: str,
        actor: str = "manual",
    ) -> CuratedMemoryEntry:
        self._assert_write_allowed(session_id, actor=actor)
        entries = self._load_entries(session_id)
        for index, entry in enumerate(entries):
            if entry.entry_id != entry_id:
                continue
            next_title = (title or entry.title).strip()
            next_content = (content or entry.content).strip()
            if not next_title or not next_content:
                raise ValueError("title and content are required")
            candidate = MemoryCandidate(
                kind=entry.kind,
                title=next_title,
                content=next_content,
                reason=reason,
                source=source or entry.source or "manual",
                confidence=confidence if confidence is not None else entry.confidence,
                last_verified_at=last_verified_at if last_verified_at is not None else entry.last_verified_at,
            )
            decision = self._evaluate_write_candidate(session_id, candidate, actor=actor)
            if not decision.accepted:
                self._append_audit_event(
                    session_id,
                    "write_rejected",
                    actor=actor,
                    action="update_curated",
                    entry_id=entry_id,
                    reason=reason,
                    rejection_reason=decision.reason,
                )
                raise ValueError(decision.reason)
            updated = CuratedMemoryEntry(
                entry_id=entry.entry_id,
                kind=entry.kind,
                title=next_title,
                content=next_content,
                source=source or entry.source or "manual",
                created_at=entry.created_at or self._now_iso(),
                updated_at=self._now_iso(),
                confidence=self._normalize_confidence(confidence if confidence is not None else entry.confidence),
                last_verified_at=last_verified_at if last_verified_at is not None else entry.last_verified_at,
                status=entry.status,
                supersedes=entry.supersedes,
            )
            entries[index] = updated
            self._write_entries(session_id, entries)
            self._append_audit_event(
                session_id,
                "write_accepted",
                actor=actor,
                action="update_curated",
                outcome="updated",
                entry_id=updated.entry_id,
                reason=reason,
            )
            return updated
        raise FileNotFoundError(f"Unknown memory entry: {entry_id}")

    def archive_curated_entry(self, session_id: str, entry_id: str, *, reason: str, actor: str = "manual") -> CuratedMemoryEntry:
        self._assert_write_allowed(session_id, actor=actor)
        entries = self._load_entries(session_id)
        for index, entry in enumerate(entries):
            if entry.entry_id != entry_id:
                continue
            archived = CuratedMemoryEntry(
                **{
                    **asdict(entry),
                    "updated_at": self._now_iso(),
                    "status": "archived",
                }
            )
            entries[index] = archived
            self._write_entries(session_id, entries)
            self._append_audit_event(
                session_id,
                "write_accepted",
                actor=actor,
                action="archive_curated",
                outcome="archived",
                entry_id=archived.entry_id,
                reason=reason,
            )
            return archived
        raise FileNotFoundError(f"Unknown memory entry: {entry_id}")

    def supersede_curated_entry(
        self,
        session_id: str,
        entry_id: str,
        *,
        title: str | None,
        content: str,
        reason: str,
        source: str = "manual",
        confidence: float | None = None,
        last_verified_at: str | None = None,
        actor: str = "manual",
    ) -> CuratedMemoryEntry:
        self._assert_write_allowed(session_id, actor=actor)
        entries = self._load_entries(session_id)
        for index, entry in enumerate(entries):
            if entry.entry_id != entry_id:
                continue
            next_title = (title or entry.title).strip()
            next_content = content.strip()
            if not next_title or not next_content:
                raise ValueError("title and content are required")
            candidate = MemoryCandidate(
                kind=entry.kind,
                title=next_title,
                content=next_content,
                reason=reason,
                source=source,
                confidence=confidence,
                last_verified_at=last_verified_at,
            )
            decision = self._evaluate_write_candidate(session_id, candidate, actor=actor)
            if not decision.accepted:
                self._append_audit_event(
                    session_id,
                    "write_rejected",
                    actor=actor,
                    action="supersede_curated",
                    entry_id=entry_id,
                    reason=reason,
                    rejection_reason=decision.reason,
                )
                raise ValueError(decision.reason)

            now = self._now_iso()
            replaced = CuratedMemoryEntry(
                **{
                    **asdict(entry),
                    "updated_at": now,
                    "status": "superseded",
                }
            )
            replacement = CuratedMemoryEntry(
                entry_id=self._new_entry_id(),
                kind=entry.kind,
                title=next_title,
                content=next_content,
                source=source,
                created_at=now,
                updated_at=now,
                confidence=self._normalize_confidence(confidence),
                last_verified_at=last_verified_at,
                status="active",
                supersedes=entry.entry_id,
            )
            entries[index] = replaced
            entries.append(replacement)
            self._write_entries(session_id, entries)
            self._append_audit_event(
                session_id,
                "write_accepted",
                actor=actor,
                action="supersede_curated",
                outcome="superseded",
                entry_id=entry.entry_id,
                replacement_entry_id=replacement.entry_id,
                reason=reason,
            )
            return replacement
        raise FileNotFoundError(f"Unknown memory entry: {entry_id}")

    def delete_curated_entry(
        self,
        session_id: str,
        *,
        entry_id: str | None = None,
        kind: str | None = None,
        title: str | None = None,
        reason: str = "manual delete",
        actor: str = "manual",
    ) -> Path:
        self._assert_write_allowed(session_id, actor=actor)
        entries = self._load_entries(session_id)
        target_id = entry_id
        if target_id is None:
            normalized_kind = self._normalize_kind(kind or "")
            normalized_title = str(title or "").strip()
            if not normalized_title:
                raise ValueError("title is required")
            existing = next(
                (
                    entry
                    for entry in entries
                    if entry.kind == normalized_kind and entry.title == normalized_title
                ),
                None,
            )
            if existing is None:
                raise FileNotFoundError(f"Unknown memory entry: {normalized_kind}/{normalized_title}")
            target_id = existing.entry_id

        next_entries = [entry for entry in entries if entry.entry_id != target_id]
        if len(next_entries) == len(entries):
            raise FileNotFoundError(f"Unknown memory entry: {target_id}")
        path = self._write_entries(session_id, next_entries)
        self._append_audit_event(
            session_id,
            "write_accepted",
            actor=actor,
            action="delete_curated",
            outcome="deleted",
            entry_id=target_id,
            reason=reason,
        )
        return path

    def list_daily_logs(self, session_id: str) -> list[str]:
        daily_root = self.daily_root(session_id)
        if not daily_root.exists():
            return []
        return sorted(
            [path.stem for path in daily_root.glob("*.md") if path.is_file()],
            reverse=True,
        )

    def read_daily_log(self, session_id: str, date: str) -> str:
        path = self.daily_log_path(session_id, date)
        if not path.exists():
            raise FileNotFoundError(f"Unknown daily log: {date}")
        return path.read_text(encoding="utf-8")

    def append_daily_log(
        self,
        session_id: str,
        *,
        title: str,
        content: str,
        date: str | None = None,
        reason: str = "manual daily append",
        source: str = "manual",
        actor: str = "manual",
    ) -> Path:
        normalized_title = title.strip()
        normalized_content = content.strip()
        if not normalized_title:
            raise ValueError("title is required")
        if not normalized_content:
            raise ValueError("content is required")
        self._assert_write_allowed(session_id, actor=actor)
        candidate = MemoryCandidate(
            kind="note",
            title=normalized_title,
            content=normalized_content,
            reason=reason,
            source=source,
            confidence=1.0,
        )
        decision = self._evaluate_write_candidate(session_id, candidate, actor=actor)
        if not decision.accepted:
            self._append_audit_event(
                session_id,
                "write_rejected",
                actor=actor,
                action="append_daily",
                title=normalized_title,
                reason=reason,
                rejection_reason=decision.reason,
            )
            raise ValueError(decision.reason)

        resolved_date = self._normalize_date(date)
        path = self.daily_log_path(session_id, resolved_date)
        timestamp = datetime.now().strftime("%H:%M")
        block = f"## {timestamp} {normalized_title}\n\n{normalized_content.rstrip()}\n"
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"# Daily Memory {resolved_date}\n\n", encoding="utf-8")
            with path.open("a", encoding="utf-8") as handle:
                if path.stat().st_size > 0:
                    handle.write("\n")
                handle.write(block)
        self._append_audit_event(
            session_id,
            "write_accepted",
            actor=actor,
            action="append_daily",
            outcome="appended",
            date=resolved_date,
            title=normalized_title,
            reason=reason,
        )
        return path

    def search(
        self,
        session_id: str,
        *,
        query: str,
        limit: int | None = None,
        include_daily: bool = True,
        include_inactive: bool = False,
        actor: str | None = None,
    ) -> list[MemorySearchHit]:
        normalized_query = self._normalize_query(query)
        configured_limit = self.runtime_config.memory.max_search_results
        requested_limit = limit or configured_limit
        max_results = max(1, min(requested_limit, configured_limit))
        hits: list[MemorySearchHit] = []

        curated_path = self.ensure_curated_document(session_id)
        for entry in self._load_entries(session_id):
            if not include_inactive and entry.status != "active":
                continue
            score = self._score_curated_entry(entry, normalized_query)
            if score <= 0:
                continue
            hits.append(
                MemorySearchHit(
                    scope="curated",
                    path=self._display_path(curated_path),
                    entry_id=entry.entry_id,
                    kind=entry.kind,
                    title=entry.title,
                    snippet=self._snippet(entry.content, normalized_query),
                    status=entry.status,
                    confidence=entry.confidence,
                    created_at=entry.created_at,
                    updated_at=entry.updated_at,
                    last_verified_at=entry.last_verified_at,
                    score=score,
                )
            )

        if include_daily:
            for date in self.list_daily_logs(session_id):
                daily_path = self.daily_log_path(session_id, date)
                daily_text = daily_path.read_text(encoding="utf-8")
                for entry in self._parse_daily_entries(
                    daily_text,
                    date=date,
                    path=self._display_path(daily_path),
                ):
                    score = self._score_daily_entry(entry, normalized_query)
                    if score <= 0:
                        continue
                    hits.append(
                        MemorySearchHit(
                            scope="daily",
                            path=entry.path,
                            title=entry.heading,
                            snippet=self._snippet(entry.content, normalized_query),
                            kind="daily",
                            date=entry.date,
                            score=score,
                        )
                    )

        ordered_hits = sorted(
            hits,
            key=lambda hit: (
                -hit.score,
                hit.updated_at or "",
                hit.date or "",
                hit.title.lower(),
            ),
        )[:max_results]
        if actor is not None:
            self._append_audit_event(
                session_id,
                "search",
                actor=actor,
                query=query,
                include_daily=include_daily,
                include_inactive=include_inactive,
                hit_entry_ids=[hit.entry_id for hit in ordered_hits if hit.entry_id],
                hit_titles=[hit.title for hit in ordered_hits],
            )
        return ordered_hits

    def build_auto_memory_note(self, session_id: str, query: str) -> MemoryPromptSelection | None:
        if not self.is_enabled() or not self.runtime_config.memory.auto_load_memory:
            return None
        settings = self.get_settings(session_id)
        if not settings.auto_retrieve_enabled:
            return None

        selected_entries = self._select_prompt_entries(session_id, query, limit=_AUTO_RETRIEVAL_LIMIT)
        if not selected_entries:
            return None

        max_chars = max(1, self.runtime_config.memory.max_auto_chars)
        lines = ["Session memory:"]
        included_entries: list[CuratedMemoryEntry] = []

        for entry in selected_entries:
            label = f"- [{entry.kind}] {entry.title}"
            details: list[str] = []
            if entry.updated_at:
                details.append(f"updated {entry.updated_at[:10]}")
            if entry.confidence is not None:
                details.append(f"confidence {entry.confidence:.2f}")
            if entry.last_verified_at:
                details.append(f"verified {entry.last_verified_at[:10]}")
            if details:
                label += f" ({', '.join(details)})"
            candidate_lines = [label, f"  {self._compact_text(entry.content, limit=220)}"]
            candidate_note = "\n".join(lines + [""] + candidate_lines)
            if len(candidate_note) > max_chars:
                break
            lines.extend([""] + candidate_lines)
            included_entries.append(entry)

        if not included_entries:
            return None

        note = "\n".join(lines)
        if len(note) > max_chars:
            note = note[: max_chars - 1].rstrip() + "…"
        return MemoryPromptSelection(note=note, entries=included_entries)

    def auto_save_turn(
        self,
        session_id: str,
        *,
        turn_id: int,
        user_message: str,
        assistant_message: str,
    ) -> list[CuratedMemoryEntry]:
        settings = self.get_settings(session_id)
        if settings.mode != "auto":
            return []

        candidates = self._extract_auto_candidates(user_message, assistant_message)
        if not candidates:
            return []

        saved_entries: list[CuratedMemoryEntry] = []
        for candidate in candidates:
            decision = evaluate_autonomous_write(candidate, settings)
            if not decision.accepted:
                self._append_audit_event(
                    session_id,
                    "auto_candidate_rejected",
                    turn_id=turn_id,
                    kind=candidate.kind,
                    title=candidate.title,
                    reason=candidate.reason,
                    rejection_reason=decision.reason,
                )
                continue
            try:
                saved_entry = self.upsert_curated_entry(
                    session_id,
                    kind=candidate.kind,
                    title=candidate.title,
                    content=candidate.content,
                    reason=candidate.reason,
                    source=candidate.source,
                    confidence=candidate.confidence,
                    last_verified_at=candidate.last_verified_at,
                    actor="auto",
                )
            except ValueError as exc:
                self._append_audit_event(
                    session_id,
                    "auto_candidate_rejected",
                    turn_id=turn_id,
                    kind=candidate.kind,
                    title=candidate.title,
                    reason=candidate.reason,
                    rejection_reason=str(exc),
                )
                continue
            saved_entries.append(saved_entry)

        if saved_entries:
            self._append_audit_event(
                session_id,
                "auto_writeback_completed",
                turn_id=turn_id,
                entry_ids=[entry.entry_id for entry in saved_entries],
            )
        return saved_entries

    def record_prompt_injection(
        self,
        session_id: str,
        *,
        turn_id: int,
        query: str,
        entry_ids: list[str],
    ) -> None:
        if not entry_ids:
            return
        entries = [
            entry
            for entry in self._load_entries(session_id)
            if entry.entry_id in set(entry_ids)
        ]
        self._append_audit_event(
            session_id,
            "prompt_injection",
            turn_id=turn_id,
            query=query,
            entry_ids=[entry.entry_id for entry in entries],
            entry_titles=[entry.title for entry in entries],
        )

    def read_audit_log(self, session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        path = self.audit_path(session_id)
        if not path.exists():
            return []
        events = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if limit is not None:
            return events[-limit:]
        return events

    def describe_workspace(self, session_id: str) -> dict[str, object]:
        path = self.ensure_curated_document(session_id)
        content = path.read_text(encoding="utf-8")
        entries = self._parse_curated_entries(content)
        section_counts = {kind: 0 for kind in _SECTION_HEADINGS}
        status_counts = {status: 0 for status in _VALID_MEMORY_STATUSES}
        for entry in entries:
            section_counts[entry.kind] += 1
            status_counts[entry.status] += 1
        settings = self.get_settings(session_id)
        return {
            "session_id": session_id,
            "root_dir": str(self.session_root(session_id)),
            "document_path": str(path),
            "settings_path": str(self.settings_path(session_id)),
            "audit_path": str(self.audit_path(session_id)),
            "content_preview": self._snippet(content, ""),
            "document_char_count": len(content),
            "entry_count": len(entries),
            "section_counts": section_counts,
            "status_counts": status_counts,
            "daily_files": self.list_daily_logs(session_id),
            "settings": asdict(settings),
        }

    def daily_log_path(self, session_id: str, date: str) -> Path:
        normalized_date = self._normalize_date(date)
        return self.daily_root(session_id) / f"{normalized_date}.md"

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_root))
        except ValueError:
            return str(path)

    def _lookup_session(self, session_id: str):
        if self._session_lookup is None:
            return None
        return self._session_lookup(session_id)

    def _write_entries(self, session_id: str, entries: list[CuratedMemoryEntry]) -> Path:
        path = self.ensure_curated_document(session_id)
        with self._lock:
            path.write_text(self._render_curated_document(entries), encoding="utf-8")
        return path

    def _load_entries(self, session_id: str) -> list[CuratedMemoryEntry]:
        content = self.read_curated_document(session_id)
        return self._parse_curated_entries(content)

    def _assert_write_allowed(self, session_id: str, *, actor: str) -> None:
        settings = self.get_settings(session_id)
        if actor == "auto":
            if not settings.autonomous_write_enabled:
                raise ValueError("session memory mode does not allow autonomous writeback")
            return
        if not settings.manual_write_enabled:
            raise ValueError("session memory mode does not allow manual writes")

    def _evaluate_write_candidate(self, session_id: str, candidate: MemoryCandidate, *, actor: str):
        settings = self.get_settings(session_id)
        if actor == "auto":
            return evaluate_autonomous_write(candidate, settings)
        return evaluate_manual_write(candidate, settings)

    def _extract_auto_candidates(self, user_message: str, assistant_message: str) -> list[MemoryCandidate]:
        del user_message
        candidates_by_key: dict[tuple[str, str], MemoryCandidate] = {}
        for raw_line in assistant_message.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _AUTO_CANDIDATE_RE.match(line)
            if match is None:
                continue
            kind = self._normalize_kind(match.group("kind"))
            title = match.group("title").strip()
            content = match.group("content").strip()
            if not title or not content:
                continue
            candidate = MemoryCandidate(
                kind=kind,
                title=title,
                content=content,
                reason="assistant emitted explicit managed memory line",
                source="assistant_auto",
                confidence=0.8,
            )
            candidates_by_key[(candidate.kind, candidate.title.lower())] = candidate
        return list(candidates_by_key.values())

    def _select_prompt_entries(self, session_id: str, query: str, *, limit: int) -> list[CuratedMemoryEntry]:
        normalized_query = self._normalize_query(query)
        scored_entries = [
            (self._score_curated_entry(entry, normalized_query), entry)
            for entry in self._load_entries(session_id)
            if entry.status == "active"
        ]
        selected = [
            entry
            for score, entry in sorted(
                scored_entries,
                key=lambda item: (
                    -item[0],
                    item[1].updated_at or "",
                    item[1].entry_id,
                ),
            )
            if score > 0
        ]
        return selected[:limit]

    def _append_audit_event(self, session_id: str, event: str, **fields: Any) -> None:
        path = self.audit_path(session_id)
        payload = {
            "timestamp": self._now_iso(),
            "session_id": session_id,
            "event": event,
            **fields,
        }
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _normalize_kind(kind: str) -> str:
        normalized = str(kind or "").strip().lower()
        if normalized not in _SECTION_HEADINGS:
            raise ValueError("kind must be one of: fact, decision, task, note")
        return normalized

    @staticmethod
    def _normalize_status(status: str) -> str:
        normalized = str(status or "").strip().lower()
        if normalized not in _VALID_MEMORY_STATUSES:
            raise ValueError("status must be one of: active, archived, superseded")
        return normalized

    @staticmethod
    def _normalize_date(date: str | None) -> str:
        resolved = date or datetime.now().strftime("%Y-%m-%d")
        try:
            datetime.strptime(resolved, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("date must be in YYYY-MM-DD format") from exc
        return resolved

    @staticmethod
    def _normalize_query(query: str) -> str:
        normalized = str(query or "").strip().lower()
        if not normalized:
            raise ValueError("query is required")
        return normalized

    @staticmethod
    def _normalize_confidence(confidence: float | None) -> float | None:
        if confidence is None:
            return None
        value = float(confidence)
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return round(value, 3)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.split(r"[^a-z0-9]+", text.lower()) if token}

    @classmethod
    def _score_curated_entry(cls, entry: CuratedMemoryEntry, query: str) -> float:
        title_text = entry.title.lower()
        body_text = entry.content.lower()
        query_tokens = cls._tokenize(query)
        title_tokens = cls._tokenize(entry.title)
        body_tokens = cls._tokenize(entry.content)
        title_overlap = len(query_tokens & title_tokens)
        body_overlap = len(query_tokens & body_tokens)
        if query not in title_text and query not in body_text and not title_overlap and not body_overlap:
            return 0.0

        score = 0.0
        if title_text == query:
            score += 1500.0
        elif query in title_text:
            score += 1000.0
        score += title_overlap * 120.0
        score += body_overlap * 25.0
        score += {"decision": 12.0, "task": 10.0, "fact": 8.0, "note": 4.0}[entry.kind]
        if entry.confidence is not None:
            score += entry.confidence * 20.0
        score += cls._freshness_bonus(entry.updated_at or entry.created_at)
        score += cls._verification_bonus(entry.last_verified_at)
        return score

    @classmethod
    def _score_daily_entry(cls, entry: DailyMemoryEntry, query: str) -> float:
        heading_text = entry.heading.lower()
        body_text = entry.content.lower()
        query_tokens = cls._tokenize(query)
        heading_tokens = cls._tokenize(entry.heading)
        body_tokens = cls._tokenize(entry.content)
        heading_overlap = len(query_tokens & heading_tokens)
        body_overlap = len(query_tokens & body_tokens)
        if query not in heading_text and query not in body_text and not heading_overlap and not body_overlap:
            return 0.0

        score = 0.0
        if heading_text == query:
            score += 700.0
        elif query in heading_text:
            score += 400.0
        score += heading_overlap * 70.0
        score += body_overlap * 18.0
        score += cls._freshness_bonus(entry.date, daily=True)
        return score

    @staticmethod
    def _freshness_bonus(value: str | None, *, daily: bool = False) -> float:
        if not value:
            return 0.0
        try:
            parsed = (
                datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if daily
                else datetime.fromisoformat(value).astimezone(timezone.utc)
            )
        except ValueError:
            return 0.0
        age_days = max((datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0, 0.0)
        if age_days <= 7:
            return 8.0
        if age_days <= 30:
            return 4.0
        if age_days <= 90:
            return 1.0
        return -2.0

    @staticmethod
    def _verification_bonus(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            parsed = datetime.fromisoformat(value).astimezone(timezone.utc)
        except ValueError:
            return 0.0
        age_days = max((datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0, 0.0)
        if age_days <= 30:
            return 5.0
        if age_days <= 90:
            return 1.0
        return -4.0

    @staticmethod
    def _snippet(text: str, query: str, *, limit: int = 240) -> str:
        compact = " ".join(text.strip().split())
        if not compact:
            return ""
        if not query:
            return compact[:limit]
        index = compact.lower().find(query)
        if index < 0:
            return compact[:limit]
        start = max(index - 40, 0)
        end = min(start + limit, len(compact))
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(compact) else ""
        return f"{prefix}{compact[start:end]}{suffix}"

    @staticmethod
    def _compact_text(text: str, *, limit: int) -> str:
        compact = " ".join(text.strip().split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 1].rstrip() + "…"

    def _parse_curated_entries(self, content: str) -> list[CuratedMemoryEntry]:
        entries: list[CuratedMemoryEntry] = []
        current_kind: str | None = None
        current_title: str | None = None
        current_lines: list[str] = []

        def flush() -> None:
            nonlocal current_title, current_lines
            if current_kind is None or current_title is None:
                current_title = None
                current_lines = []
                return
            entries.append(
                self._build_entry_from_block(
                    kind=current_kind,
                    title=current_title,
                    lines=current_lines,
                )
            )
            current_title = None
            current_lines = []

        for line in content.splitlines():
            if line.startswith("## "):
                flush()
                current_kind = _HEADING_TO_KIND.get(line[3:].strip())
                continue
            heading_match = _ENTRY_HEADING_RE.match(line)
            if heading_match:
                flush()
                if current_kind is None:
                    continue
                current_title = heading_match.group("title").strip()
                continue
            if current_title is not None:
                current_lines.append(line)

        flush()
        return entries

    def _build_entry_from_block(self, *, kind: str, title: str, lines: list[str]) -> CuratedMemoryEntry:
        metadata: dict[str, str] = {}
        body_lines: list[str] = []
        index = 0

        while index < len(lines) and not lines[index].strip():
            index += 1

        while index < len(lines):
            line = lines[index]
            if not line.startswith("- "):
                break
            match = _METADATA_LINE_RE.match(line)
            if match is None:
                raise ValueError(f"Malformed memory entry metadata line: {line}")
            key = match.group("key").strip()
            if key not in _VALID_ENTRY_METADATA_KEYS:
                raise ValueError(f"Unsupported memory entry metadata key: {key}")
            metadata[key] = match.group("value").strip() or None
            index += 1

        while index < len(lines) and not lines[index].strip():
            index += 1
        body_lines = lines[index:]

        entry = CuratedMemoryEntry(
            entry_id=str(metadata.get("entry_id") or self._legacy_entry_id(kind, title)),
            kind=self._normalize_kind(str(metadata.get("kind") or kind)),
            title=str(metadata.get("title") or title).strip(),
            content="\n".join(body_lines).strip(),
            source=str(metadata.get("source") or "manual"),
            created_at=metadata.get("created_at"),
            updated_at=metadata.get("updated_at"),
            confidence=self._parse_confidence(metadata.get("confidence")),
            last_verified_at=metadata.get("last_verified_at"),
            status=self._normalize_status(str(metadata.get("status") or "active")),
            supersedes=metadata.get("supersedes"),
        )
        if metadata.get("title") and entry.title != title.strip():
            raise ValueError(f"Entry heading and title metadata disagree for {title}")
        return entry

    @staticmethod
    def _parse_daily_entries(content: str, *, date: str, path: str) -> list[DailyMemoryEntry]:
        entries: list[DailyMemoryEntry] = []
        current_heading: str | None = None
        current_lines: list[str] = []

        def flush() -> None:
            nonlocal current_heading, current_lines
            if current_heading is not None:
                entries.append(
                    DailyMemoryEntry(
                        date=date,
                        heading=current_heading,
                        content="\n".join(current_lines).strip(),
                        path=path,
                    )
                )
            current_heading = None
            current_lines = []

        for line in content.splitlines():
            if line.startswith("## "):
                flush()
                current_heading = line[3:].strip()
                continue
            if current_heading is not None:
                current_lines.append(line)
        flush()
        return entries

    @staticmethod
    def _parse_confidence(value: str | None) -> float | None:
        if value is None or value == "":
            return None
        return SessionMemoryStore._normalize_confidence(float(value))

    def _normalize_entry(
        self,
        entry: CuratedMemoryEntry,
        *,
        default_source: str,
        preserve_timestamps: bool,
    ) -> CuratedMemoryEntry:
        now = self._now_iso()
        created_at = entry.created_at if preserve_timestamps and entry.created_at else now
        updated_at = entry.updated_at if preserve_timestamps and entry.updated_at else now
        return CuratedMemoryEntry(
            entry_id=entry.entry_id or self._new_entry_id(),
            kind=self._normalize_kind(entry.kind),
            title=entry.title.strip(),
            content=entry.content.strip(),
            source=(entry.source or default_source).strip(),
            created_at=created_at,
            updated_at=updated_at,
            confidence=self._normalize_confidence(entry.confidence),
            last_verified_at=entry.last_verified_at,
            status=self._normalize_status(entry.status),
            supersedes=entry.supersedes,
        )

    def _render_curated_document(self, entries: list[CuratedMemoryEntry]) -> str:
        entries_by_kind: dict[str, list[CuratedMemoryEntry]] = {kind: [] for kind in _SECTION_HEADINGS}
        for entry in entries:
            normalized = self._normalize_entry(
                entry,
                default_source="manual",
                preserve_timestamps=True,
            )
            entries_by_kind.setdefault(normalized.kind, []).append(normalized)

        lines = ["# Session Memory", ""]
        for kind, heading in _SECTION_HEADINGS.items():
            lines.append(f"## {heading}")
            lines.append("")
            for entry in entries_by_kind.get(kind, []):
                lines.extend(self._render_single_entry_lines(entry))
            if lines[-1] != "":
                lines.append("")
        rendered = "\n".join(lines).rstrip()
        return rendered + "\n"

    def _render_single_entry_lines(self, entry: CuratedMemoryEntry) -> list[str]:
        normalized = self._normalize_entry(entry, default_source="manual", preserve_timestamps=True)
        lines = [
            f"### {normalized.title}",
            f"- entry_id: {normalized.entry_id}",
            f"- kind: {normalized.kind}",
            f"- title: {normalized.title}",
            f"- source: {normalized.source}",
            f"- created_at: {normalized.created_at}",
            f"- updated_at: {normalized.updated_at}",
            f"- confidence: {normalized.confidence if normalized.confidence is not None else ''}",
            f"- last_verified_at: {normalized.last_verified_at or ''}",
            f"- status: {normalized.status}",
            f"- supersedes: {normalized.supersedes or ''}",
            "",
        ]
        if normalized.content:
            lines.append(normalized.content.rstrip())
            lines.append("")
        return lines

    @staticmethod
    def _legacy_entry_id(kind: str, title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "entry"
        return f"legacy-{kind}-{slug}"

    @staticmethod
    def _new_entry_id() -> str:
        return f"mem_{uuid4().hex[:12]}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = ["SessionMemoryStore"]
