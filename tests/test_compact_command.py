"""Tests for the /compact slash command."""

import io
import json
from pathlib import Path

from rich.console import Console

from src.agent import Agent
from src.commands import builtin
from src.commands.registry import CommandRegistry
from src.config import Config
from src.context import Context
from src.memory import SessionMemory
from src.skills import SkillManager
from src.tools.skill import LoadSkillTool
from src.tools import ToolRegistry


def write_skill(skill_dir: Path, *, name: str = "pdf") -> None:
    """Create a minimal skill bundle for /compact tests."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Handle PDFs\n"
        "metadata:\n"
        "  short-description: PDF workflows\n"
        "---\n\n"
        "Use the PDF skill.\n",
        encoding="utf-8",
    )


class StubLLM:
    """Minimal LLM stub that can summarize compacted turns."""

    provider = "stub"
    model = "stub-model"
    base_url = None
    logger = None

    def chat(self, messages, tools=None, log_context=None):
        return {
            "role": "assistant",
            "content": json.dumps(
                {
                    "goal": ["Keep the session short"],
                    "active_work": ["Review earlier turns"],
                    "important_decisions_in_effect": ["none"],
                    "key_discoveries": ["none"],
                    "completed_work": ["Reviewed earlier turns"],
                    "working_set_files": ["none"],
                    "open_loops": ["none"],
                    "next_steps": ["Continue"],
                    "risks_or_blockers": ["none"],
                }
            ),
        }, object()


def make_console(buffer: io.StringIO) -> Console:
    """Create a deterministic Rich console."""
    return Console(file=buffer, force_terminal=False, color_system=None, width=120)


def add_turns(session_context: Context, count: int, *, size: int = 120) -> None:
    """Add complete user/assistant turns to the session context."""
    for index in range(count):
        session_context.add_message("user", f"user {index} " + ("u" * size))
        session_context.add_message("assistant", f"assistant {index} " + ("a" * size))


def create_compact_env(temp_dir, monkeypatch, *, with_memory: bool = False, logging_enabled: bool = False):
    """Build a command environment with compaction support."""
    monkeypatch.setenv("BABYCLAW_TEST", "true")
    cfg = Config.reload()
    cfg.logging.enabled = logging_enabled
    cfg.logging.async_mode = False
    cfg.logging.log_dir = str(temp_dir / "sessions")
    cfg.llm.context_window = 400
    cfg.context.auto_compact = True
    cfg.context.auto_compact_threshold = 0.85
    cfg.context.target_usage_after_compaction = 0.60
    cfg.context.min_recent_turns = 2
    cfg.memory.enabled = with_memory
    cfg.memory.root_dir = str(temp_dir / "sessions")

    repo_root = temp_dir / "repo"
    write_skill(repo_root / ".babyclaw" / "skills" / "pdf")
    skill_manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    skill_manager.discover()

    tools = ToolRegistry()
    tools.register(LoadSkillTool(skill_manager))
    session_context = Context.create(cwd=str(repo_root))
    memory_store = SessionMemory(repo_root=temp_dir, runtime_config=cfg) if with_memory else None
    agent = Agent(
        StubLLM(),
        tools,
        session_context,
        skill_manager=skill_manager,
        runtime_config=cfg,
        memory_store=memory_store,
    )

    registry = CommandRegistry()
    builtin.register_all(registry)
    command_context = {
        "agent": agent,
        "session_context": session_context,
        "skill_manager": skill_manager,
        "memory_store": memory_store,
    }
    return registry, agent, session_context, command_context


def test_compact_shows_status(temp_dir, monkeypatch):
    """`/compact` should show current compaction status."""
    registry, _, _, command_context = create_compact_env(temp_dir, monkeypatch)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact", console, command_context)

    text = output.getvalue()
    assert "Context Compaction" in text
    assert "Auto-compaction" in text
    assert "Auto decision" in text
    assert "Decision detail" in text
    assert "Threshold" in text
    assert "Configured recent turns retained" in text
    assert "Effective recent turns retained" in text


def test_compact_show_displays_summary(temp_dir, monkeypatch):
    """`/compact show` should print the current rolling summary."""
    registry, agent, session_context, command_context = create_compact_env(temp_dir, monkeypatch)
    add_turns(session_context, 5)
    agent.context_compaction.compact_now(agent, "manual_command", force=True)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact show", console, command_context)

    text = output.getvalue()
    assert "Compacted Summary" in text
    assert "Session handoff for earlier turns:" in text


def test_compact_now_forces_compaction(temp_dir, monkeypatch):
    """`/compact now` should summarize older turns immediately."""
    registry, _, session_context, command_context = create_compact_env(temp_dir, monkeypatch)
    add_turns(session_context, 5)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact now", console, command_context)

    text = output.getvalue()
    assert "Manual Compaction" in text
    assert "1. Inspect current context:" in text
    assert "2. Apply adaptive retention:" in text
    assert "3. Select turns to summarize:" in text
    assert "5. Replace older raw history" in text
    assert "6. Recalculate baseline usage:" in text
    assert "Context compacted:" in text
    assert session_context.get_summary() is not None


def test_compact_auto_on_toggles_session_state(temp_dir, monkeypatch):
    """`/compact auto on` should enable session-local auto-compaction."""
    registry, _, session_context, command_context = create_compact_env(temp_dir, monkeypatch)
    session_context.set_auto_compaction(False)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact auto on", console, command_context)

    assert session_context.is_auto_compaction_enabled() is True
    assert "enabled for this session" in output.getvalue()


def test_compact_auto_off_toggles_session_state(temp_dir, monkeypatch):
    """`/compact auto off` should disable session-local auto-compaction."""
    registry, _, session_context, command_context = create_compact_env(temp_dir, monkeypatch)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact auto off", console, command_context)

    assert session_context.is_auto_compaction_enabled() is False
    assert "disabled for this session" in output.getvalue()


def test_compact_now_reports_safe_noop(temp_dir, monkeypatch):
    """Forcing compaction with one turn should explain why compaction cannot proceed."""
    registry, _, session_context, command_context = create_compact_env(temp_dir, monkeypatch)
    add_turns(session_context, 1, size=120)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact now", console, command_context)

    text = output.getvalue()
    assert "Context compaction skipped" in text
    assert "Manual Compaction" in text
    assert "1. Inspect current context:" in text
    assert "2. Apply adaptive retention:" in text
    assert "3. Stop:" in text
    assert "Compaction requires at least 2 complete turns" in text
    assert "Complete turns: 1" in text
    assert "Force mode: yes" in text
    assert "Effective recent turns retained: 1" in text


def test_compact_now_compacts_small_history_below_configured_retention(temp_dir, monkeypatch):
    """Two turns should compact even when configured recent retention is larger."""
    registry, _, session_context, command_context = create_compact_env(temp_dir, monkeypatch)
    command_context["agent"].context_compaction.policy = command_context["agent"].context_compaction.policy.__class__(
        auto_compact=True,
        auto_compact_threshold=0.85,
        target_usage_after_compaction=0.60,
        min_recent_turns=6,
    )
    add_turns(session_context, 2, size=120)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact now", console, command_context)

    text = output.getvalue()
    assert "Context compacted:" in text
    assert session_context.get_summary() is not None
    assert len(session_context.get_complete_turns()) == 1


def test_compact_history_displays_recent_snapshots(temp_dir, monkeypatch):
    """`/compact history` should render recent compaction snapshots from the session ledger."""
    registry, _agent, session_context, command_context = create_compact_env(
        temp_dir,
        monkeypatch,
        logging_enabled=True,
    )
    add_turns(session_context, 5)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact now", console, command_context)

    output = io.StringIO()
    console = make_console(output)
    registry.execute("/compact history", console, command_context)

    text = output.getvalue()
    assert "Compaction History" in text
    assert "manual_command" in text
    assert "Session handoff for earlier turns:" in text


def test_compact_help_renders_full_manual(temp_dir, monkeypatch):
    """`/compact help` should render the command manual."""
    registry, _, _, command_context = create_compact_env(temp_dir, monkeypatch)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact help", console, command_context)

    text = output.getvalue()
    assert "Command: /compact" in text
    assert "/compact now" in text
    assert "Compaction is session-local." in text


def test_compact_help_aliases_render_manual(temp_dir, monkeypatch):
    """`/compact --help` and `/compact -h` should render the manual."""
    registry, _, _, command_context = create_compact_env(temp_dir, monkeypatch)

    for command_line in ("/compact --help", "/compact -h"):
        output = io.StringIO()
        console = make_console(output)

        registry.execute(command_line, console, command_context)

        assert "Command: /compact" in output.getvalue()


def test_compact_targeted_help_for_now(temp_dir, monkeypatch):
    """`/compact help now` should render the targeted subcommand help."""
    registry, _, _, command_context = create_compact_env(temp_dir, monkeypatch)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact help now", console, command_context)

    text = output.getvalue()
    assert "Command: /compact (now)" in text
    assert "/compact now" in text
    assert "Compact immediately" in text


def test_compact_unknown_subcommand_prints_manual(temp_dir, monkeypatch):
    """Unknown `/compact` subcommands should print the full manual after the error."""
    registry, _, _, command_context = create_compact_env(temp_dir, monkeypatch)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact helo", console, command_context)

    text = output.getvalue()
    assert "Unknown /compact subcommand: helo" in text
    assert "Command: /compact" in text


def test_compact_auto_missing_argument_shows_targeted_help(temp_dir, monkeypatch):
    """`/compact auto` should explain the missing action and show `auto` help."""
    registry, _, _, command_context = create_compact_env(temp_dir, monkeypatch)
    output = io.StringIO()
    console = make_console(output)

    registry.execute("/compact auto", console, command_context)

    text = output.getvalue()
    assert "Missing /compact auto action" in text
    assert "Command: /compact (auto)" in text
    assert "/compact auto on" in text
    assert "/compact auto off" in text
