"""Built-in tools for managed file-backed session memory."""

from __future__ import annotations

from src.memory import SessionMemoryStore
from src.tools import Tool, ToolResult


def _format_search_results(query: str, hits) -> str:
    if not hits:
        return f"No memory hits found for: {query}"
    lines = [f"Memory hits for: {query}"]
    for index, hit in enumerate(hits, start=1):
        scope = hit.scope if hit.date is None else f"{hit.scope}:{hit.date}"
        lines.extend(
            [
                f"{index}. {hit.title}",
                f"   Scope: {scope}",
                f"   Path: {hit.path}",
                f"   Entry ID: {hit.entry_id or '-'}",
                f"   Kind: {hit.kind or '-'}",
                f"   Status: {hit.status or '-'}",
                f"   Confidence: {hit.confidence if hit.confidence is not None else '-'}",
                f"   Snippet: {hit.snippet}",
            ]
        )
    return "\n".join(lines)


def _format_entry(entry) -> str:
    lines = [
        f"Entry ID: {entry.entry_id}",
        f"Kind: {entry.kind}",
        f"Title: {entry.title}",
        f"Status: {entry.status}",
        f"Source: {entry.source or '-'}",
        f"Created: {entry.created_at or '-'}",
        f"Updated: {entry.updated_at or '-'}",
        f"Confidence: {entry.confidence if entry.confidence is not None else '-'}",
        f"Last Verified: {entry.last_verified_at or '-'}",
        f"Supersedes: {entry.supersedes or '-'}",
        "",
        entry.content,
    ]
    return "\n".join(lines).strip()


class MemoryReadTool(Tool):
    """Read curated or daily Markdown memory for the current session."""

    name = "memory_read"
    description = (
        "Inspect the current session memory workspace when you need exact memory contents. "
        "Use this for targeted reference or debugging of MEMORY.md, one curated section or entry, "
        "or a specific daily log file. Prefer memory_search for normal recall, and avoid reading the "
        "full MEMORY.md unless you genuinely need broad inspection."
    )
    parameters = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["curated", "curated_section", "curated_entry", "daily_list", "daily_file"],
                "description": (
                    "Which memory surface to inspect. Prefer curated_entry or curated_section for focused reads; "
                    "use curated only for broad MEMORY.md inspection."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["fact", "decision", "task", "note"],
                "description": (
                    "Curated section kind when target=curated_section. fact=user identity/project facts/preferences, "
                    "decision=chosen rules/approaches, task=follow-up obligations, note=useful but less stable context."
                ),
            },
            "entry_id": {
                "type": "string",
                "description": "Curated entry id when target=curated_entry and you need one exact durable memory.",
            },
            "date": {
                "type": "string",
                "description": "Daily log date in YYYY-MM-DD format when target=daily_file.",
            },
            "include_inactive": {
                "type": "boolean",
                "description": "Whether archived/superseded entries should be included in curated reads.",
            },
        },
        "required": ["target"],
    }

    def __init__(self, memory_store: SessionMemoryStore) -> None:
        self.memory_store = memory_store

    def execute(self, context, **kwargs) -> ToolResult:
        try:
            target = self._require_param(kwargs, "target")
            session_id = context.session_id
            include_inactive = bool(kwargs.get("include_inactive", True))

            if target == "curated":
                path = self.memory_store.curated_document_path(session_id)
                content = self.memory_store.read_curated_document(session_id)
                return ToolResult(success=True, data=f"{path}:\n\n{content}")

            if target == "curated_section":
                kind = self._require_param(kwargs, "kind")
                content = self.memory_store.read_curated_section(
                    session_id,
                    kind,
                    include_inactive=include_inactive,
                )
                return ToolResult(success=True, data=content)

            if target == "curated_entry":
                entry_id = self._require_param(kwargs, "entry_id")
                entry = self.memory_store.read_entry(session_id, entry_id)
                return ToolResult(success=True, data=_format_entry(entry))

            if target == "daily_list":
                logs = self.memory_store.list_daily_logs(session_id)
                if not logs:
                    return ToolResult(success=True, data="No daily memory logs exist yet.")
                return ToolResult(success=True, data="\n".join(logs))

            if target == "daily_file":
                date = self._require_param(kwargs, "date")
                path = self.memory_store.daily_log_path(session_id, date)
                content = self.memory_store.read_daily_log(session_id, date)
                return ToolResult(success=True, data=f"{path}:\n\n{content}")

            return ToolResult(
                success=False,
                error="target must be one of: curated, curated_section, curated_entry, daily_list, daily_file",
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class MemorySearchTool(Tool):
    """Search the current session memory files."""

    name = "memory_search"
    description = (
        "Search the current session memory before asking the user again for known context. "
        "This is the default recall tool when you suspect the session may already contain relevant "
        "identity, current workstream, preferences, constraints, decisions, tasks, or other durable notes. "
        "Search daily logs only when recent journal-style context may matter."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Case-insensitive query for existing session memory. Use it before re-asking for known identity, "
                    "project context, preferences, constraints, decisions, or tasks."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of hits to return. Keep this small for targeted recall.",
            },
            "include_daily": {
                "type": "boolean",
                "description": "Whether to include daily logs in the search for temporary or journal-style context.",
            },
            "include_inactive": {
                "type": "boolean",
                "description": "Whether archived and superseded curated entries should be searched.",
            },
        },
        "required": ["query"],
    }

    def __init__(self, memory_store: SessionMemoryStore) -> None:
        self.memory_store = memory_store

    def execute(self, context, **kwargs) -> ToolResult:
        try:
            query = self._require_param(kwargs, "query")
            limit = kwargs.get("limit")
            include_daily = bool(kwargs.get("include_daily", True))
            include_inactive = bool(kwargs.get("include_inactive", False))
            hits = self.memory_store.search(
                context.session_id,
                query=query,
                limit=limit,
                include_daily=include_daily,
                include_inactive=include_inactive,
                actor="tool",
            )
            return ToolResult(success=True, data=_format_search_results(query, hits))
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class MemoryWriteTool(Tool):
    """Mutate the current session memory workspace."""

    name = "memory_write"
    description = (
        "Persist durable, safe session memory that should matter in later turns. "
        "Use this when the user reveals information worth remembering across the session, especially "
        "their name or preferred form of address, what project or feature they are working on, stable "
        "preferences or constraints, decisions the session should keep following, and tasks or reminders "
        "the assistant should retain. Prefer updating existing memory over creating duplicates. "
        "Use upsert_curated for durable facts, preferences, current work, decisions, and tasks; use "
        "append_daily only for temporary journal-style notes. Do not store transient chatter, one-off "
        "temporary details, secrets, tokens, passwords, or low-confidence guesses."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "upsert_curated",
                    "update_curated",
                    "archive_curated",
                    "supersede_curated",
                    "delete_curated",
                    "append_daily",
                ],
                "description": (
                    "Structured memory operation to perform. Prefer upsert_curated for durable memory; use append_daily "
                    "for temporary journal notes; archive/supersede/delete manage lifecycle."
                ),
            },
            "entry_id": {
                "type": "string",
                "description": "Target entry id for update, archive, supersede, or delete actions.",
            },
            "kind": {
                "type": "string",
                "enum": ["fact", "decision", "task", "note"],
                "description": (
                    "Curated memory kind. fact=user identity/project facts/preferences/constraints, "
                    "decision=chosen rules or approaches, task=follow-up obligations or reminders, "
                    "note=useful context that matters later but is less stable."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short durable memory title, for example user-name, current-feature, or deploy-order.",
            },
            "content": {
                "type": "string",
                "description": (
                    "Memory body text. Store only durable, safe information that will help later turns; do not include "
                    "secrets, passwords, or transient chatter."
                ),
            },
            "date": {
                "type": "string",
                "description": "Daily log date in YYYY-MM-DD format for append_daily.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Why storing or changing this memory will help later turns. Explain the future usefulness instead "
                    "of just repeating the memory content."
                ),
            },
            "source": {
                "type": "string",
                "description": (
                    "Source label such as assistant_explicit, tool, web, or cli. Defaults to assistant_explicit for "
                    "normal conversation-driven memory writes."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "Optional confidence score between 0 and 1. Avoid writing low-confidence guesses.",
            },
            "last_verified_at": {
                "type": "string",
                "description": "Optional verification timestamp in ISO8601 format.",
            },
        },
        "required": ["action", "reason"],
    }

    def __init__(self, memory_store: SessionMemoryStore) -> None:
        self.memory_store = memory_store

    def execute(self, context, **kwargs) -> ToolResult:
        try:
            action = self._require_param(kwargs, "action")
            reason = self._require_param(kwargs, "reason")
            session_id = context.session_id
            source = str(kwargs.get("source") or "assistant_explicit")
            confidence = kwargs.get("confidence")
            last_verified_at = kwargs.get("last_verified_at")

            if action == "upsert_curated":
                kind = self._require_param(kwargs, "kind")
                title = self._require_param(kwargs, "title")
                content = self._require_param(kwargs, "content")
                entry = self.memory_store.upsert_curated_entry(
                    session_id,
                    kind=kind,
                    title=title,
                    content=content,
                    reason=reason,
                    source=source,
                    confidence=confidence,
                    last_verified_at=last_verified_at,
                    actor="manual",
                )
                return ToolResult(success=True, data=f"Updated curated memory:\n\n{_format_entry(entry)}")

            if action == "update_curated":
                entry_id = self._require_param(kwargs, "entry_id")
                entry = self.memory_store.update_curated_entry(
                    session_id,
                    entry_id,
                    title=kwargs.get("title"),
                    content=kwargs.get("content"),
                    confidence=confidence,
                    source=source,
                    last_verified_at=last_verified_at,
                    reason=reason,
                    actor="manual",
                )
                return ToolResult(success=True, data=f"Updated curated memory:\n\n{_format_entry(entry)}")

            if action == "archive_curated":
                entry_id = self._require_param(kwargs, "entry_id")
                entry = self.memory_store.archive_curated_entry(
                    session_id,
                    entry_id,
                    reason=reason,
                    actor="manual",
                )
                return ToolResult(success=True, data=f"Archived curated memory:\n\n{_format_entry(entry)}")

            if action == "supersede_curated":
                entry_id = self._require_param(kwargs, "entry_id")
                content = self._require_param(kwargs, "content")
                entry = self.memory_store.supersede_curated_entry(
                    session_id,
                    entry_id,
                    title=kwargs.get("title"),
                    content=content,
                    reason=reason,
                    source=source,
                    confidence=confidence,
                    last_verified_at=last_verified_at,
                    actor="manual",
                )
                return ToolResult(success=True, data=f"Superseded curated memory:\n\n{_format_entry(entry)}")

            if action == "delete_curated":
                path = self.memory_store.delete_curated_entry(
                    session_id,
                    entry_id=kwargs.get("entry_id"),
                    kind=kwargs.get("kind"),
                    title=kwargs.get("title"),
                    reason=reason,
                    actor="manual",
                )
                return ToolResult(success=True, data=f"Deleted curated memory entry from {path}")

            if action == "append_daily":
                title = self._require_param(kwargs, "title")
                content = self._require_param(kwargs, "content")
                path = self.memory_store.append_daily_log(
                    session_id,
                    title=title,
                    content=content,
                    date=kwargs.get("date"),
                    reason=reason,
                    source=source,
                    actor="manual",
                )
                return ToolResult(success=True, data=f"Appended daily memory entry to {path}")

            return ToolResult(
                success=False,
                error="action must be one of: upsert_curated, update_curated, archive_curated, supersede_curated, delete_curated, append_daily",
            )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
