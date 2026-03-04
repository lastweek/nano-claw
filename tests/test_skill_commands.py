"""Tests for /skill slash commands."""

import io

from rich.console import Console

from src.commands import builtin
from src.commands.registry import CommandRegistry
from src.context import Context
from src.skills import SkillManager


def write_skill(skill_dir, name="pdf", description="Handle PDFs", short_description="PDF workflows"):
    """Create a skill bundle for command tests."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "metadata:\n"
        f"  short-description: {short_description}\n"
        "---\n\n"
        "Use the skill.\n",
        encoding="utf-8",
    )


def create_console(buffer: io.StringIO) -> Console:
    """Create a deterministic Rich console."""
    return Console(file=buffer, force_terminal=False, color_system=None, width=120)


def create_skill_context(temp_dir):
    """Create a command registry and skill command context."""
    repo_root = temp_dir / "repo"
    write_skill(repo_root / ".nano-claw" / "skills" / "pdf")
    write_skill(
        temp_dir / "user-skills" / "terraform",
        name="terraform",
        description="Handle Terraform",
        short_description="Terraform workflows",
    )
    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    registry = CommandRegistry()
    builtin.register_all(registry)

    session_context = Context.create(cwd=str(repo_root))
    command_context = {
        "skill_manager": manager,
        "session_context": session_context,
    }
    return registry, manager, session_context, command_context


def test_skill_command_lists_available_skills(temp_dir):
    """`/skill` should list discovered skills and active state."""
    registry, _, _, command_context = create_skill_context(temp_dir)
    output = io.StringIO()
    console = create_console(output)

    executed = registry.execute("/skill", console, command_context)

    assert executed is True
    text = output.getvalue()
    assert "Available Skills" in text
    assert "pdf" in text
    assert "terraform" in text
    assert "PDF workflows" in text
    assert "Terraform workflows" in text
    assert "Catalog" in text
    assert text.count("yes") >= 2


def test_skill_use_and_clear_commands_update_pins(temp_dir):
    """`/skill use` and `/skill clear` should manage pinned skills."""
    registry, _, session_context, command_context = create_skill_context(temp_dir)
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill use pdf", console, command_context)
    assert session_context.get_active_skills() == ["pdf"]

    registry.execute("/skill clear pdf", console, command_context)
    assert session_context.get_active_skills() == []

    text = output.getvalue()
    assert "Pinned skill:" in text
    assert "Unpinned skill:" in text


def test_skill_clear_all_clears_everything(temp_dir):
    """`/skill clear all` should drop all pinned skills."""
    registry, _, session_context, command_context = create_skill_context(temp_dir)
    session_context.activate_skill("pdf")
    session_context.activate_skill("other")
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill clear all", console, command_context)

    assert session_context.get_active_skills() == []
    assert "Cleared 2 pinned skill(s)" in output.getvalue()


def test_skill_show_displays_metadata_and_resources(temp_dir):
    """`/skill show` should render metadata for the selected skill."""
    registry, manager, _, command_context = create_skill_context(temp_dir)
    skill = manager.get_skill("pdf")
    assert skill is not None
    (skill.root_dir / "references").mkdir(parents=True, exist_ok=True)
    ref_file = skill.root_dir / "references" / "guide.md"
    ref_file.write_text("guide", encoding="utf-8")
    manager.discover()

    output = io.StringIO()
    console = create_console(output)
    registry.execute("/skill show pdf", console, command_context)

    text = output.getvalue()
    assert "Skill: pdf" in text
    assert "Description:" in text
    assert "Catalog Visible:" in text
    assert "Skill File:" in text
    assert str(ref_file.resolve()) in text


def test_skill_show_reports_user_global_skill_as_catalog_visible(temp_dir):
    """User-global skills should report catalog visibility in `/skill show`."""
    registry, _, _, command_context = create_skill_context(temp_dir)
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill show terraform", console, command_context)

    text = output.getvalue()
    assert "Skill: terraform" in text
    assert "Catalog Visible:" in text
    assert "yes" in text


def test_skill_reload_prunes_missing_pins(temp_dir):
    """`/skill reload` should drop pinned skills that disappeared from disk."""
    registry, manager, session_context, command_context = create_skill_context(temp_dir)
    session_context.activate_skill("pdf")
    skill = manager.get_skill("pdf")
    assert skill is not None
    skill.skill_file.unlink()
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill reload", console, command_context)

    assert session_context.get_active_skills() == []
    text = output.getvalue()
    assert "Removed missing pinned skill: pdf" in text
    assert "Reloaded 1 skill(s)" in text


def test_skill_command_reports_unknown_skill(temp_dir):
    """Unknown skill targets should return clear errors."""
    registry, _, _, command_context = create_skill_context(temp_dir)
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill show missing", console, command_context)

    assert "Unknown skill: missing" in output.getvalue()


def test_skill_help_renders_full_manual(temp_dir):
    """`/skill help` should render the command manual."""
    registry, _, _, command_context = create_skill_context(temp_dir)
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill help", console, command_context)

    text = output.getvalue()
    assert "Command: /skill" in text
    assert "/skill use <name>" in text
    assert "/skill reload" in text


def test_skill_targeted_help_renders_selected_subcommand(temp_dir):
    """`/skill help clear` should render targeted subcommand help."""
    registry, _, _, command_context = create_skill_context(temp_dir)
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill help clear", console, command_context)

    text = output.getvalue()
    assert "Command: /skill (clear)" in text
    assert "/skill clear <name|all>" in text
    assert "Unpin one skill or clear all pinned skills." in text


def test_skill_unknown_subcommand_prints_manual(temp_dir):
    """Unknown `/skill` subcommands should print the full manual after the error."""
    registry, _, _, command_context = create_skill_context(temp_dir)
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill helo", console, command_context)

    text = output.getvalue()
    assert "Unknown /skill subcommand: helo" in text
    assert "Command: /skill" in text


def test_skill_use_missing_argument_shows_targeted_help(temp_dir):
    """`/skill use` should explain the missing name and show `use` help."""
    registry, _, _, command_context = create_skill_context(temp_dir)
    output = io.StringIO()
    console = create_console(output)

    registry.execute("/skill use", console, command_context)

    text = output.getvalue()
    assert "Missing skill name for /skill use" in text
    assert "Command: /skill (use)" in text
    assert "/skill use <name>" in text
