"""Prompt-construction policies for automatic memory injection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.memory.types import MemoryPromptItem, MemoryPromptSelection

if TYPE_CHECKING:
    from src.memory.session_memory import SessionMemory


def build_prompt_selection(
    memory: "SessionMemory",
    session_id: str,
    query: str,
) -> MemoryPromptSelection | None:
    """Build one bounded prompt-memory selection for the active session."""
    settings = memory.get_settings(session_id)
    policy_name = settings.prompt_policy
    if policy_name == "curated_only":
        curated_items = _select_curated_items(
            memory,
            session_id,
            query,
            limit=memory.runtime_config.memory.max_auto_curated_hits,
        )
        return _render_selection(
            memory,
            session_id=session_id,
            policy_name=policy_name,
            curated_items=curated_items,
            daily_items=[],
        )

    if policy_name == "curated_plus_recent_daily":
        curated_items = _select_curated_items(
            memory,
            session_id,
            query,
            limit=memory.runtime_config.memory.max_auto_curated_hits,
        )
        daily_items = _select_daily_items(
            memory,
            session_id,
            query,
            recent_daily_days=memory.runtime_config.memory.recent_daily_days,
            limit=memory.runtime_config.memory.max_auto_daily_hits,
        )
        return _render_selection(
            memory,
            session_id=session_id,
            policy_name=policy_name,
            curated_items=curated_items,
            daily_items=daily_items,
        )

    if policy_name == "search_all_ranked":
        hits = memory.search(
            session_id,
            query=query,
            limit=memory.runtime_config.memory.max_search_results,
            include_daily=True,
            include_inactive=False,
            recent_daily_days=None,
            actor=None,
            policy_name=policy_name,
        )
        curated_items, daily_items = _items_from_ranked_hits(memory, session_id, hits)
        return _render_selection(
            memory,
            session_id=session_id,
            policy_name=policy_name,
            curated_items=curated_items[: memory.runtime_config.memory.max_auto_curated_hits],
            daily_items=daily_items[: memory.runtime_config.memory.max_auto_daily_hits],
        )

    raise ValueError(f"Unsupported prompt policy: {policy_name}")


def _select_curated_items(
    memory: "SessionMemory",
    session_id: str,
    query: str,
    *,
    limit: int,
) -> list[MemoryPromptItem]:
    normalized_query = memory._normalize_query(query)
    curated_path = memory.ensure_curated_document(session_id)
    scored_entries = [
        (memory._score_curated_entry(entry, normalized_query), entry)
        for entry in memory._load_entries(session_id)
        if entry.status == "active"
    ]
    ordered_entries = [
        (score, entry)
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
    return [
        MemoryPromptItem(
            scope="curated",
            title=entry.title,
            content=entry.content,
            path=memory._display_path(curated_path),
            entry_id=entry.entry_id,
            kind=entry.kind,
            score=score,
            updated_at=entry.updated_at,
            last_verified_at=entry.last_verified_at,
            confidence=entry.confidence,
        )
        for score, entry in ordered_entries[:limit]
    ]


def _select_daily_items(
    memory: "SessionMemory",
    session_id: str,
    query: str,
    *,
    recent_daily_days: int | None,
    limit: int,
) -> list[MemoryPromptItem]:
    normalized_query = memory._normalize_query(query)
    scored_items: list[tuple[float, MemoryPromptItem]] = []
    for date in memory._list_daily_logs(session_id, recent_daily_days=recent_daily_days):
        daily_path = memory.daily_log_path(session_id, date)
        daily_text = daily_path.read_text(encoding="utf-8")
        for entry in memory._parse_daily_entries(
            daily_text,
            date=date,
            path=memory._display_path(daily_path),
        ):
            score = memory._score_daily_entry(entry, normalized_query)
            if score <= 0:
                continue
            scored_items.append(
                (
                    score,
                    MemoryPromptItem(
                        scope="daily",
                        title=entry.heading,
                        content=entry.content,
                        path=entry.path,
                        date=entry.date,
                        score=score,
                    ),
                )
            )
    ordered_items = [
        item
        for score, item in sorted(
            scored_items,
            key=lambda pair: (
                -pair[0],
                pair[1].date or "",
                pair[1].title.lower(),
            ),
        )
        if score > 0
    ]
    return ordered_items[:limit]


def _items_from_ranked_hits(memory: "SessionMemory", session_id: str, hits) -> tuple[list[MemoryPromptItem], list[MemoryPromptItem]]:
    curated_entries = {
        entry.entry_id: entry
        for entry in memory._load_entries(session_id)
        if entry.status == "active"
    }
    daily_entries: dict[tuple[str, str, str], MemoryPromptItem] = {}
    for date in memory._list_daily_logs(session_id, recent_daily_days=None):
        daily_path = memory.daily_log_path(session_id, date)
        daily_text = daily_path.read_text(encoding="utf-8")
        for entry in memory._parse_daily_entries(
            daily_text,
            date=date,
            path=memory._display_path(daily_path),
        ):
            daily_entries[(entry.date, entry.heading, entry.path)] = MemoryPromptItem(
                scope="daily",
                title=entry.heading,
                content=entry.content,
                path=entry.path,
                date=entry.date,
                score=0.0,
            )

    curated_items: list[MemoryPromptItem] = []
    daily_items: list[MemoryPromptItem] = []
    seen_curated: set[str] = set()
    seen_daily: set[tuple[str, str, str]] = set()

    for hit in hits:
        if hit.scope == "curated" and hit.entry_id:
            entry = curated_entries.get(hit.entry_id)
            if entry is None or entry.entry_id in seen_curated:
                continue
            seen_curated.add(entry.entry_id)
            curated_items.append(
                MemoryPromptItem(
                    scope="curated",
                    title=entry.title,
                    content=entry.content,
                    path=hit.path,
                    entry_id=entry.entry_id,
                    kind=entry.kind,
                    score=hit.score,
                    updated_at=entry.updated_at,
                    last_verified_at=entry.last_verified_at,
                    confidence=entry.confidence,
                )
            )
            continue

        if hit.scope == "daily" and hit.date:
            key = (hit.date, hit.title, hit.path)
            item = daily_entries.get(key)
            if item is None or key in seen_daily:
                continue
            seen_daily.add(key)
            daily_items.append(
                MemoryPromptItem(
                    scope="daily",
                    title=item.title,
                    content=item.content,
                    path=item.path,
                    date=item.date,
                    score=hit.score,
                )
            )

    return curated_items, daily_items


def _render_selection(
    memory: "SessionMemory",
    *,
    session_id: str,
    policy_name: str,
    curated_items: list[MemoryPromptItem],
    daily_items: list[MemoryPromptItem],
) -> MemoryPromptSelection | None:
    max_chars = max(1, memory.runtime_config.memory.max_auto_chars)
    lines: list[str] = []
    included_items: list[MemoryPromptItem] = []

    lines, included_items = _append_section(
        memory,
        session_id=session_id,
        policy_name=policy_name,
        existing_lines=lines,
        existing_items=included_items,
        heading="Session memory:",
        items=curated_items,
        max_chars=max_chars,
    )
    daily_heading = "Recent daily notes:" if policy_name == "curated_plus_recent_daily" else "Daily notes:"
    lines, included_items = _append_section(
        memory,
        session_id=session_id,
        policy_name=policy_name,
        existing_lines=lines,
        existing_items=included_items,
        heading=daily_heading,
        items=daily_items,
        max_chars=max_chars,
    )

    if not included_items:
        memory._emit_memory_debug(
            session_id,
            "debug_prompt_item_skipped",
            prompt_policy=policy_name,
            reason="no prompt memory items matched the current query",
        )
        return None
    note = "\n".join(lines)
    if len(note) > max_chars:
        note = note[: max_chars - 1].rstrip() + "…"
    return MemoryPromptSelection(
        policy_name=policy_name,
        note=note,
        items=included_items,
    )


def _append_section(
    memory: "SessionMemory",
    *,
    session_id: str,
    policy_name: str,
    existing_lines: list[str],
    existing_items: list[MemoryPromptItem],
    heading: str,
    items: list[MemoryPromptItem],
    max_chars: int,
) -> tuple[list[str], list[MemoryPromptItem]]:
    if not items:
        return existing_lines, existing_items

    next_lines = list(existing_lines)
    next_items = list(existing_items)
    if next_lines:
        next_lines.append("")
    section_start = list(next_lines)
    section_start.append(heading)
    section_items: list[MemoryPromptItem] = []

    for item in items:
        candidate_lines = section_start if not section_items else list(next_lines)
        if not section_items:
            candidate_lines = list(section_start)
        else:
            candidate_lines = list(next_lines)
        candidate_lines.extend([""] + _render_item_lines(memory, item))
        candidate_note = "\n".join(candidate_lines)
        if len(candidate_note) > max_chars:
            memory._emit_memory_debug(
                session_id,
                "debug_prompt_item_skipped",
                prompt_policy=policy_name,
                scope=item.scope,
                title=item.title,
                path=item.path,
                date=item.date,
                score=item.score,
                reason="prompt memory char budget exceeded",
            )
            break
        next_lines = candidate_lines
        section_items.append(item)
        next_items.append(item)

    if not section_items:
        return existing_lines, existing_items
    return next_lines, next_items


def _render_item_lines(memory: "SessionMemory", item: MemoryPromptItem) -> list[str]:
    if item.scope == "curated":
        label = f"- [{item.kind}] {item.title}"
        details: list[str] = []
        if item.updated_at:
            details.append(f"updated {item.updated_at[:10]}")
        if item.confidence is not None:
            details.append(f"confidence {item.confidence:.2f}")
        if item.last_verified_at:
            details.append(f"verified {item.last_verified_at[:10]}")
        if details:
            label += f" ({', '.join(details)})"
        return [label, f"  {memory._compact_text(item.content, limit=220)}"]

    label = f"- [{item.date}] {item.title}" if item.date else f"- {item.title}"
    return [label, f"  {memory._compact_text(item.content, limit=220)}"]
