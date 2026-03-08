"""Tests for managed Markdown session memory."""

from datetime import datetime, timedelta
from rich.console import Console

from src.agent import Agent
from src.agent_turn_prep import build_conversation_messages, prepare_turn_input
from src.commands import CommandRegistry, builtin
from src.context import Context
from src.memory import MemoryWriteCandidate, SessionMemory
from src.tools import ToolRegistry
from src.tools.memory import MemoryReadTool, MemorySearchTool, MemoryWriteTool


def _make_store(temp_dir, http_runtime_config):
    http_runtime_config.memory.enabled = True
    return SessionMemory(repo_root=temp_dir, runtime_config=http_runtime_config)


def test_memory_package_exports_new_public_names():
    """The memory package should expose the renamed public API."""
    assert SessionMemory.__name__ == "SessionMemory"
    assert MemoryWriteCandidate.__name__ == "MemoryWriteCandidate"


class _DummyLLM:
    """Minimal LLM stub for prompt-building tests."""

    provider = "ollama"
    model = "fake-model"
    base_url = "http://localhost:11434/v1"
    logger = None


def test_memory_store_resolves_into_unified_session_directory(temp_dir, http_runtime_config):
    """Session memory should live inside the shared per-session sessions root."""
    store = _make_store(temp_dir, http_runtime_config)

    path = store.ensure_curated_document("sess_move")

    assert path == (temp_dir / "sessions" / path.parent.name / "MEMORY.md").resolve()
    assert path.parent.name.endswith("-sess_move")
    assert path.parent.parent == (temp_dir / "sessions").resolve()
    assert store.settings_path("sess_move").name == "memory-settings.json"
    assert store.audit_path("sess_move").name == "memory-audit.jsonl"


def test_memory_store_parses_empty_default_document(temp_dir, http_runtime_config):
    """A new session should get the canonical empty MEMORY.md layout."""
    store = _make_store(temp_dir, http_runtime_config)
    path = store.ensure_curated_document("sess_empty")
    content = path.read_text(encoding="utf-8")

    assert content.startswith("# Session Memory")
    assert "## Facts" in content
    assert "## Decisions" in content
    assert store.list_entries("sess_empty") == []


def test_memory_store_round_trips_structured_entries_with_metadata(temp_dir, http_runtime_config):
    """Structured writes should render metadata and preserve semantic fields on parse."""
    store = _make_store(temp_dir, http_runtime_config)

    created = store.upsert_curated_entry(
        "sess_meta",
        kind="decision",
        title="deploy-order",
        content="Run migrations before deploy.",
        reason="test create",
        source="cli",
        confidence=0.9,
        last_verified_at="2026-03-06T12:00:00+00:00",
    )
    parsed = store.read_entry("sess_meta", created.entry_id)

    assert parsed.entry_id == created.entry_id
    assert parsed.kind == "decision"
    assert parsed.title == "deploy-order"
    assert parsed.source == "cli"
    assert parsed.confidence == 0.9
    assert parsed.last_verified_at == "2026-03-06T12:00:00+00:00"
    assert "- entry_id:" in store.read_curated_document("sess_meta")


def test_memory_store_tolerates_legacy_manual_document_then_reconciles_on_structured_write(temp_dir, http_runtime_config):
    """Legacy heading-only Markdown should remain readable and normalize on the next structured write."""
    store = _make_store(temp_dir, http_runtime_config)

    store.write_curated_document(
        "sess_legacy",
        "# Session Memory\n\n## Facts\n\n### Deploy\n\nShip carefully.\n",
        reason="legacy import",
    )
    legacy_entry = store.list_entries("sess_legacy")[0]
    assert legacy_entry.entry_id.startswith("legacy-fact-")

    updated = store.upsert_curated_entry(
        "sess_legacy",
        kind="fact",
        title="Deploy",
        content="Ship carefully and verify health.",
        reason="normalize legacy entry",
        source="http_api",
    )
    assert updated.entry_id == legacy_entry.entry_id
    assert "- entry_id:" in store.read_curated_document("sess_legacy")
    assert "verify health" in store.read_curated_document("sess_legacy")


def test_memory_store_rejects_malformed_entry_metadata(temp_dir, http_runtime_config):
    """Malformed metadata should fail loudly instead of silently corrupting memory state."""
    store = _make_store(temp_dir, http_runtime_config)

    bad_document = """# Session Memory

## Facts

### Deploy
- source manual

Run migrations first.
"""

    try:
        store.write_curated_document("sess_bad", bad_document, reason="bad document")
    except ValueError as exc:
        assert "Malformed memory entry metadata line" in str(exc)
    else:
        raise AssertionError("Expected malformed metadata to fail")


def test_memory_search_ranks_exact_title_hit_above_body_only_match(temp_dir, http_runtime_config):
    """Exact title matches should outrank body-only lexical hits."""
    store = _make_store(temp_dir, http_runtime_config)
    store.upsert_curated_entry(
        "sess_search",
        kind="decision",
        title="deploy order",
        content="Run migrations before deploy.",
        reason="seed",
    )
    store.upsert_curated_entry(
        "sess_search",
        kind="note",
        title="release note",
        content="The deploy order changed last week.",
        reason="seed",
    )

    hits = store.search("sess_search", query="deploy order", include_daily=False)
    assert hits[0].title == "deploy order"
    assert hits[0].scope == "curated"


def test_archived_and_superseded_entries_are_filtered_from_default_retrieval(temp_dir, http_runtime_config):
    """Prompt retrieval and default search should only use active curated entries."""
    store = _make_store(temp_dir, http_runtime_config)
    first = store.upsert_curated_entry(
        "sess_lifecycle",
        kind="fact",
        title="provider",
        content="Use provider A.",
        reason="seed",
    )
    store.archive_curated_entry("sess_lifecycle", first.entry_id, reason="archive old")
    base = store.upsert_curated_entry(
        "sess_lifecycle",
        kind="decision",
        title="deploy-order",
        content="Run migrations before deploy.",
        reason="seed",
    )
    replacement = store.supersede_curated_entry(
        "sess_lifecycle",
        base.entry_id,
        title="deploy-order v2",
        content="Run migrations, then verify health.",
        reason="corrected",
    )

    hits = store.search("sess_lifecycle", query="provider", include_daily=False)
    assert hits == []

    selection = store.build_prompt_memory("sess_lifecycle", "deploy")
    assert selection is not None
    assert [item.entry_id for item in selection.items if item.entry_id] == [replacement.entry_id]
    assert "deploy-order v2" in selection.note
    assert "provider" not in selection.note


def test_curated_only_prompt_policy_keeps_daily_logs_out_of_prompt_injection(temp_dir, http_runtime_config):
    """The curated_only prompt policy should ignore daily logs during prompt construction."""
    store = _make_store(temp_dir, http_runtime_config)
    store.update_settings("sess_daily", prompt_policy="curated_only")
    store.upsert_curated_entry(
        "sess_daily",
        kind="fact",
        title="deploy-order",
        content="Run migrations before deploy.",
        reason="seed",
    )
    store.append_daily_log(
        "sess_daily",
        date="2026-03-06",
        title="daily-note",
        content="This evidence should be searchable but not auto-injected.",
        reason="seed daily",
    )

    hits = store.search("sess_daily", query="evidence", include_daily=True)
    assert any(hit.scope == "daily" for hit in hits)

    selection = store.build_prompt_memory("sess_daily", "evidence")
    assert selection is None


def test_curated_plus_recent_daily_prompt_policy_includes_recent_daily_hits(temp_dir, http_runtime_config):
    """The default prompt policy should include relevant recent daily notes when they help."""
    store = _make_store(temp_dir, http_runtime_config)
    today = datetime.now().strftime("%Y-%m-%d")
    store.append_daily_log(
        "sess_recent_daily",
        date=today,
        title="sse-note",
        content="Investigated the SSE reconnect bug in the admin stream.",
        reason="seed recent daily",
    )

    selection = store.build_prompt_memory("sess_recent_daily", "SSE reconnect bug")

    assert selection is not None
    assert selection.policy_name == "curated_plus_recent_daily"
    assert any(item.scope == "daily" for item in selection.items)
    assert "Recent daily notes:" in selection.note
    assert "sse-note" in selection.note


def test_search_all_ranked_prompt_policy_can_include_older_daily_hits(temp_dir, http_runtime_config):
    """The search_all_ranked prompt policy can pull in older daily entries when they rank well."""
    store = _make_store(temp_dir, http_runtime_config)
    store.update_settings("sess_history", prompt_policy="search_all_ranked")
    old_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    store.append_daily_log(
        "sess_history",
        date=old_date,
        title="older-note",
        content="Decided to keep the release gate on green integration tests.",
        reason="seed historical daily",
    )

    selection = store.build_prompt_memory("sess_history", "release gate")

    assert selection is not None
    assert selection.policy_name == "search_all_ranked"
    assert any(item.scope == "daily" and item.date == old_date for item in selection.items)


def test_memory_policy_blocks_secret_like_content_and_manual_off_mode(temp_dir, http_runtime_config):
    """Manual writes should reject secret-like content and respect off mode."""
    store = _make_store(temp_dir, http_runtime_config)

    try:
        store.upsert_curated_entry(
            "sess_policy",
            kind="fact",
            title="api-key",
            content="OPENAI_API_KEY=sk-1234567890abcdefghijklmnop",
            reason="unsafe",
        )
    except ValueError as exc:
        assert "secret-like" in str(exc)
    else:
        raise AssertionError("Expected secret-like content to be rejected")

    store.update_settings("sess_policy", mode="off")
    try:
        store.upsert_curated_entry(
            "sess_policy",
            kind="fact",
            title="deploy-order",
            content="Run migrations first.",
            reason="manual while off",
        )
    except ValueError as exc:
        assert "manual writes" in str(exc)
    else:
        raise AssertionError("Expected off mode to block manual writes")


def test_memory_tool_descriptions_explain_when_to_search_read_and_write(temp_dir, http_runtime_config):
    """Memory tool descriptions should tell the model when to persist durable memory."""
    store = _make_store(temp_dir, http_runtime_config)
    read_tool = MemoryReadTool(store)
    search_tool = MemorySearchTool(store)
    write_tool = MemoryWriteTool(store)

    assert "Prefer memory_search for normal recall" in read_tool.description
    assert "before asking the user again" in search_tool.description
    assert "identity, current workstream, preferences, constraints, decisions, tasks" in search_tool.description
    assert "their name or preferred form of address" in write_tool.description
    assert "project or feature they are working on" in write_tool.description
    assert "Prefer updating existing memory over creating duplicates" in write_tool.description
    assert "Do not store transient chatter" in write_tool.description


def test_memory_write_defaults_to_assistant_explicit_source(temp_dir, http_runtime_config):
    """Agent-driven memory_write calls should default to assistant_explicit when source is omitted."""
    store = _make_store(temp_dir, http_runtime_config)
    context = Context.create(cwd=str(temp_dir))
    context.session_id = "sess_tool"
    result = MemoryWriteTool(store).execute(
        context,
        action="upsert_curated",
        kind="fact",
        title="user-name",
        content="The user's name is Alice.",
        reason="This identity will matter in later turns.",
    )

    assert result.success is True
    entry = store.list_entries("sess_tool")[0]
    assert entry.source == "assistant_explicit"


def test_manual_only_and_auto_modes_control_autonomous_writeback(temp_dir, http_runtime_config):
    """Autonomous writeback should respect the per-session memory mode."""
    store = _make_store(temp_dir, http_runtime_config)

    manual_only_saved = store.writeback_from_turn(
        "sess_mode",
        turn_id="turn_manual",
        user_message="Remember deploy order",
        assistant_message="Decision: deploy-order :: Run migrations before deploy.",
    )
    assert manual_only_saved == []

    store.update_settings("sess_mode", mode="auto")
    saved = store.writeback_from_turn(
        "sess_mode",
        turn_id="turn_auto",
        user_message="Remember deploy order",
        assistant_message="Decision: deploy-order :: Run migrations before deploy.",
    )
    assert len(saved) == 1
    assert saved[0].title == "deploy-order"


def test_repeated_auto_corrections_converge_to_latest_active_fact(temp_dir, http_runtime_config):
    """Repeated corrections should update the existing active memory instead of duplicating it."""
    store = _make_store(temp_dir, http_runtime_config)
    store.update_settings("sess_correction", mode="auto")

    first = store.writeback_from_turn(
        "sess_correction",
        turn_id="turn_1",
        user_message="What is deploy order?",
        assistant_message="Fact: deploy-order :: Run migrations before deploy.",
    )
    second = store.writeback_from_turn(
        "sess_correction",
        turn_id="turn_2",
        user_message="Actually revise that",
        assistant_message="Fact: deploy-order :: Run migrations before deploy and verify health.",
    )

    assert len(first) == 1
    assert len(second) == 1
    entries = store.list_entries("sess_correction", include_inactive=True)
    assert len(entries) == 1
    assert entries[0].content == "Run migrations before deploy and verify health."


def test_build_prompt_includes_memory_guidance_when_memory_tools_are_available(temp_dir, http_runtime_config):
    """Build-mode prompt should tell the model when to search and persist durable session memory."""
    store = _make_store(temp_dir, http_runtime_config)
    registry = ToolRegistry()
    registry.register(MemoryReadTool(store))
    registry.register(MemorySearchTool(store))
    registry.register(MemoryWriteTool(store))

    context = Context.create(cwd=str(temp_dir))
    agent = Agent(
        _DummyLLM(),
        registry,
        context,
        runtime_config=http_runtime_config,
        memory_store=store,
    )

    prompt = agent._build_system_prompt()

    assert "Session memory guidance:" in prompt
    assert "identity, current workstream, preferences, constraints, decisions, and tasks" in prompt
    assert "Use memory_search before re-asking for known context" in prompt
    assert "do not store secrets, passwords, tokens, or one-off temporary chatter" in prompt


def test_turn_prep_injects_top_entries_not_full_document(temp_dir, http_runtime_config):
    """Turn prep should inject a bounded entry list rather than the whole memory document."""
    store = _make_store(temp_dir, http_runtime_config)
    http_runtime_config.memory.max_auto_chars = 180
    store.upsert_curated_entry(
        "sess_turn",
        kind="decision",
        title="deploy-order",
        content="Run migrations before deploy and verify health checks.",
        reason="seed",
    )
    store.upsert_curated_entry(
        "sess_turn",
        kind="task",
        title="release-checklist",
        content="Run tests, verify migrations, then deploy.",
        reason="seed",
    )
    store.append_daily_log(
        "sess_turn",
        date="2026-03-06",
        title="daily-note",
        content="Daily memory should not be injected.",
        reason="seed daily",
    )

    class _Context:
        session_id = "sess_turn"

        @staticmethod
        def get_active_skills():
            return []

    prepared = prepare_turn_input(
        "deploy order",
        context=_Context(),
        skill_manager=None,
        memory_store=store,
        runtime_config=http_runtime_config,
    )

    assert prepared.memory_note is not None
    assert "Session memory:" in prepared.memory_note
    assert "daily-note" not in prepared.memory_note
    assert "# Session Memory" not in prepared.memory_note
    assert prepared.memory_prompt_items
    assert prepared.memory_prompt_policy == "curated_plus_recent_daily"

    messages = build_conversation_messages(
        system_message={"role": "system", "content": "base"},
        summary_message=None,
        memory_note=prepared.memory_note,
        history_messages=[],
        skill_manager=None,
        preload_skill_names=[],
        normalized_user_message=prepared.normalized_user_message,
        role_user="user",
    )
    assert messages[1]["role"] == "system"
    assert "deploy-order" in messages[1]["content"]


def test_memory_slash_commands_use_managed_store(temp_dir, http_runtime_config):
    """CLI memory commands should update structured entries and settings through the shared store."""
    store = _make_store(temp_dir, http_runtime_config)
    registry = CommandRegistry()
    builtin.register_all(registry)
    console = Console(record=True)
    session_context = type("SessionContext", (), {"session_id": "sess_cli"})()

    assert registry.execute(
        "/memory remember fact deploy-order :: Run migrations first",
        console,
        {"memory_store": store, "session_context": session_context},
    )
    assert registry.execute(
        "/memory mode auto",
        console,
        {"memory_store": store, "session_context": session_context},
    )
    assert registry.execute(
        "/memory read-policy curated_only",
        console,
        {"memory_store": store, "session_context": session_context},
    )
    assert registry.execute(
        "/memory prompt-policy search_all_ranked",
        console,
        {"memory_store": store, "session_context": session_context},
    )

    entries = store.list_entries("sess_cli")
    assert len(entries) == 1
    assert entries[0].title == "deploy-order"
    assert store.get_settings("sess_cli").mode == "auto"
    assert store.get_settings("sess_cli").read_policy == "curated_only"
    assert store.get_settings("sess_cli").prompt_policy == "search_all_ranked"


def test_prompt_injection_and_search_are_audited(temp_dir, http_runtime_config):
    """Retrieval and prompt injection should be visible in the memory audit trail."""
    store = _make_store(temp_dir, http_runtime_config)
    today = datetime.now().strftime("%Y-%m-%d")
    entry = store.upsert_curated_entry(
        "sess_audit",
        kind="fact",
        title="deploy-order",
        content="Run migrations before deploy.",
        reason="seed",
    )
    store.append_daily_log(
        "sess_audit",
        date=today,
        title="daily-audit",
        content="Investigated deploy drift yesterday.",
        reason="seed daily",
    )
    _ = store.search("sess_audit", query="deploy", include_daily=True, actor="http_api")
    store.record_prompt_injection(
        "sess_audit",
        turn_id="turn_123",
        query="deploy",
        policy_name="curated_plus_recent_daily",
        items=[
            store.build_prompt_memory("sess_audit", "deploy").items[0],
            next(item for item in store.build_prompt_memory("sess_audit", "drift").items if item.scope == "daily"),
        ],
    )

    audit = store.read_audit_log("sess_audit")
    assert any(event["event"] == "search" for event in audit)
    injection = next(event for event in audit if event["event"] == "prompt_injection")
    assert injection["turn_id"] == "turn_123"
    assert injection["entry_ids"] == [entry.entry_id]
    assert injection["prompt_policy"] == "curated_plus_recent_daily"
    assert "daily" in injection["item_scopes"]
    assert injection["daily_dates"] == [today]


def test_settings_default_to_curated_plus_recent_daily_policies(temp_dir, http_runtime_config):
    """Session settings should default prompt/read policy from config when no file exists yet."""
    store = _make_store(temp_dir, http_runtime_config)

    settings = store.get_settings("sess_defaults")

    assert settings.mode == "manual_only"
    assert settings.read_policy == "curated_plus_recent_daily"
    assert settings.prompt_policy == "curated_plus_recent_daily"


def test_read_policy_defaults_and_explicit_overrides(temp_dir, http_runtime_config):
    """The read policy should drive default memory_search behavior, but explicit args should win."""
    store = _make_store(temp_dir, http_runtime_config)
    today = datetime.now().strftime("%Y-%m-%d")
    old_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    store.append_daily_log(
        "sess_read_policy",
        date=today,
        title="recent-note",
        content="Investigated the recent reconnect issue.",
        reason="seed recent daily",
    )
    store.append_daily_log(
        "sess_read_policy",
        date=old_date,
        title="old-note",
        content="This old reconnect note should not appear by default.",
        reason="seed old daily",
    )

    plan, hits = store.search_with_policy(
        "sess_read_policy",
        query="reconnect",
        actor="tool",
    )
    assert plan.policy_name == "curated_plus_recent_daily"
    assert plan.recent_daily_days == 2
    assert all(hit.date != old_date for hit in hits if hit.scope == "daily")

    explicit_plan, explicit_hits = store.search_with_policy(
        "sess_read_policy",
        query="old reconnect note",
        include_daily=True,
        actor="tool",
    )
    assert explicit_plan.recent_daily_days is None
    assert any(hit.date == old_date for hit in explicit_hits if hit.scope == "daily")

    no_daily_plan, no_daily_hits = store.search_with_policy(
        "sess_read_policy",
        query="recent reconnect issue",
        include_daily=False,
        actor="tool",
    )
    assert no_daily_plan.include_daily is False
    assert all(hit.scope != "daily" for hit in no_daily_hits)


def test_memory_debug_emits_audit_and_console_trace(temp_dir, http_runtime_config, monkeypatch, capsys):
    """MEMORY_DEBUG should surface explicit write, search, and prompt-selection steps."""
    monkeypatch.setenv("MEMORY_DEBUG", "1")
    store = _make_store(temp_dir, http_runtime_config)
    entry = store.upsert_curated_entry(
        "sess_debug",
        kind="fact",
        title="user-name",
        content="The user's name is Alice.",
        reason="persist identity for later turns",
        source="assistant_explicit",
    )
    store.append_daily_log(
        "sess_debug",
        date="2026-03-06",
        title="recent-note",
        content="Investigated the SSE reconnect bug.",
        reason="seed recent daily",
    )
    plan, _hits = store.search_with_policy(
        "sess_debug",
        query="Alice reconnect",
        actor="tool",
        turn_id="turn_debug",
    )
    selection = store.build_prompt_memory("sess_debug", "Alice reconnect")
    assert selection is not None
    store.record_prompt_injection(
        "sess_debug",
        turn_id="turn_debug",
        query="Alice reconnect",
        policy_name=selection.policy_name,
        items=selection.items,
    )

    audit = store.read_audit_log("sess_debug")
    debug_events = {event["event"] for event in audit if event["event"].startswith("debug_")}
    assert "debug_write_requested" in debug_events
    assert "debug_write_policy_decision" in debug_events
    assert "debug_write_applied" in debug_events
    assert "debug_search_plan" in debug_events
    assert "debug_search_completed" in debug_events
    assert "debug_prompt_policy_selection" in debug_events
    assert any(
        event["event"] == "debug_prompt_item_selected" and event.get("entry_id") == entry.entry_id
        for event in audit
    )
    captured = capsys.readouterr()
    assert "[MEMORY]" in captured.out
    assert f"policy={plan.policy_name}" in captured.out
