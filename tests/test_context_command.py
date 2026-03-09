"""Tests for the /context slash command."""

import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from src.agent import Agent
from src.commands import builtin
from src.commands.registry import CommandRegistry
from src.config import Config
from src.context import CompactedContextSummary, Context
from src.skills import SkillManager
from src.tools.skill import LoadSkillTool
from src.tools import Tool, ToolRegistry, ToolResult


def write_skill(
    skill_dir: Path,
    *,
    name: str = "pdf",
    description: str = "Handle PDFs",
    short_description: str = "PDF workflows",
    body: str = "Use the skill.\n",
) -> Path:
    """Create a minimal skill bundle for context tests."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "metadata:\n"
        f"  short-description: {short_description}\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )
    return skill_file


def create_console(buffer: io.StringIO) -> Console:
    """Create a deterministic Rich console."""
    return Console(file=buffer, force_terminal=False, color_system=None, width=120)


class StubLLM:
    """Minimal LLM stub for constructing an Agent."""

    provider = "stub"
    model = "stub-model"
    base_url = None
    logger = None


class DummyTool(Tool):
    """Simple builtin tool for context usage tests."""

    name = "dummy"
    description = "A dummy tool"
    parameters = {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
        },
    }

    def execute(self, context, **kwargs):
        return ToolResult(success=True, data="ok")


class FakeMCPTool(Tool):
    """Tool with an MCP server marker for classification tests."""

    name = "deepwiki:test"
    description = "A fake MCP-backed tool"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
        },
    }

    def __init__(self):
        self._server = SimpleNamespace(name="deepwiki")

    def execute(self, context, **kwargs):
        return ToolResult(success=True, data="ok")


def create_context_command_env(
    temp_dir,
    monkeypatch,
    *,
    context_window=200_000,
    with_repo_skill=False,
    with_user_skill=False,
    with_mcp_tool=False,
):
    """Create a command registry and runtime context for /context tests."""
    monkeypatch.setenv("BABYCLAW_TEST", "true")
    cfg = Config.reload()
    cfg.logging.enabled = False
    cfg.logging.async_mode = False
    cfg.llm.context_window = context_window

    repo_root = temp_dir / "repo"
    user_root = temp_dir / "user-skills"
    repo_root.mkdir(parents=True, exist_ok=True)
    user_root.mkdir(parents=True, exist_ok=True)

    if with_repo_skill:
        write_skill(repo_root / ".babyclaw" / "skills" / "pdf")
    if with_user_skill:
        write_skill(
            user_root / "terraform",
            name="terraform",
            description="Handle Terraform",
            short_description="Terraform workflows",
        )

    skill_manager = SkillManager(repo_root=repo_root, user_root=user_root)
    skill_manager.discover()

    tools = ToolRegistry()
    tools.register(DummyTool())
    tools.register(LoadSkillTool(skill_manager))
    if with_mcp_tool:
        tools.register(FakeMCPTool())

    session_context = Context.create(cwd=str(repo_root))
    agent = Agent(StubLLM(), tools, session_context, skill_manager=skill_manager)

    registry = CommandRegistry()
    builtin.register_all(registry)
    command_context = {
        "agent": agent,
        "session_context": session_context,
        "skill_manager": skill_manager,
    }
    return registry, agent, session_context, command_context


def test_context_command_prints_summary_and_category_table(temp_dir, monkeypatch):
    """`/context` should print the top-level summary and category breakdown."""
    registry, _, _, command_context = create_context_command_env(temp_dir, monkeypatch)
    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("/context", console, command_context)

    assert executed is True
    text = output.getvalue()
    assert "Context Usage" in text
    assert "Estimated usage by category" in text
    assert "System prompt" in text
    assert "Tool schemas" in text
    assert "Skill catalog" in text
    assert "Pinned skills" in text
    assert "Compacted summary" in text
    assert "Messages" in text
    assert "Free space" in text
    assert "Auto-compaction triggers at 85% and targets 60% usage after compaction." in text


def test_context_command_is_listed_in_help(temp_dir, monkeypatch):
    """`/help` should list the new /context command."""
    registry, _, _, command_context = create_context_command_env(temp_dir, monkeypatch)
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/help", console, command_context)

    text = output.getvalue()
    assert "/context" in text
    assert "next LLM call" in text


def test_context_command_includes_skills_in_catalog_usage(temp_dir, monkeypatch):
    """Catalog-visible skills should appear in the skill breakdown."""
    registry, _, _, command_context = create_context_command_env(
        temp_dir,
        monkeypatch,
        with_repo_skill=True,
        with_user_skill=True,
    )
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/context", console, command_context)

    text = output.getvalue()
    assert "Skills" in text
    assert "pdf" in text
    assert "terraform" in text
    assert "catalog" in text


def test_context_command_includes_pinned_skills_separately(temp_dir, monkeypatch):
    """Pinned skills should be counted separately from catalog skill entries."""
    registry, _, session_context, command_context = create_context_command_env(
        temp_dir,
        monkeypatch,
        with_repo_skill=True,
    )
    session_context.activate_skill("pdf")
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/context", console, command_context)

    text = output.getvalue()
    assert "Pinned skills" in text
    assert "Skills" in text
    assert "catalog" in text
    assert "pinned" in text


def test_context_command_counts_compacted_summary_separately(temp_dir, monkeypatch):
    """Compacted summaries should be counted outside the raw Messages bucket."""
    registry, agent, session_context, command_context = create_context_command_env(
        temp_dir,
        monkeypatch,
        with_repo_skill=True,
    )
    session_context.add_message("user", "Older question")
    session_context.add_message("assistant", "Older answer")
    session_context.set_summary(
        CompactedContextSummary(
            updated_at="2026-03-02T00:00:00",
            compaction_count=1,
            covered_turn_count=1,
            covered_message_count=2,
            rendered_text="Conversation summary for earlier turns:\n- Keep going",
        )
    )
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/context", console, command_context)

    text = output.getvalue()
    assert "Compacted summary" in text
    assert "Recent messages exclude any turns already compacted into the rolling summary." in text


def test_context_command_includes_persisted_messages(temp_dir, monkeypatch):
    """Persisted conversation history should appear in the Messages section."""
    registry, _, session_context, command_context = create_context_command_env(temp_dir, monkeypatch)
    session_context.add_message("user", "Explain virtual memory in plain terms.")
    session_context.add_message("assistant", "Virtual memory gives each process its own address space.")
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/context", console, command_context)

    text = output.getvalue()
    assert "Messages" in text
    assert "user" in text
    assert "assistant" in text
    assert "Explain virtual memory in plain terms." in text


def test_context_command_handles_unknown_context_window(temp_dir, monkeypatch):
    """Unknown context windows should degrade cleanly."""
    registry, _, _, command_context = create_context_command_env(
        temp_dir,
        monkeypatch,
        context_window=None,
    )
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/context", console, command_context)

    text = output.getvalue()
    assert "/ unknown" in text
    assert "n/a" in text
    assert "Free space" not in text
    assert "Context window is not configured" in text


def test_context_command_reports_overflow(temp_dir, monkeypatch):
    """Over-limit baselines should report overflow instead of free space."""
    registry, _, session_context, command_context = create_context_command_env(
        temp_dir,
        monkeypatch,
        context_window=20,
    )
    session_context.add_message(
        "user",
        "This is a deliberately long message that should exceed the tiny configured context window.",
    )
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/context", console, command_context)

    text = output.getvalue()
    assert "Over limit" in text
    assert "Estimated baseline exceeds configured context window" in text


def test_context_command_marks_mcp_tool_schemas(temp_dir, monkeypatch):
    """MCP-backed tools should be classified distinctly in the tool schema table."""
    registry, _, _, command_context = create_context_command_env(
        temp_dir,
        monkeypatch,
        with_mcp_tool=True,
    )
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/context", console, command_context)

    text = output.getvalue()
    assert "Tool Schemas" in text
    assert "mcp:deepwiki" in text
