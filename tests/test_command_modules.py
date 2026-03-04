"""Focused tests for split command registration modules."""

from src.commands.context_cmds import register_context_commands
from src.commands.help_cmds import register_help_commands
from src.commands.mcp_cmds import register_mcp_commands
from src.commands.plan_cmds import register_plan_commands
from src.commands.registry import CommandRegistry
from src.commands.skill_cmds import register_skill_commands
from src.commands.subagent_cmds import register_subagent_commands


def test_register_help_commands_registers_help_and_tool():
    """Help command module should register the top-level help commands."""
    registry = CommandRegistry()
    register_help_commands(registry)

    assert registry.get_command("help") is not None
    assert registry.get_command("tool") is not None


def test_register_context_commands_registers_context_and_compact():
    """Context command module should register the context-related commands."""
    registry = CommandRegistry()
    register_context_commands(registry)

    assert registry.get_command("context") is not None
    assert registry.get_command("compact") is not None


def test_register_remaining_command_modules_register_expected_commands():
    """Each split module should register its expected top-level command."""
    registry = CommandRegistry()
    register_subagent_commands(registry)
    register_plan_commands(registry)
    register_mcp_commands(registry)
    register_skill_commands(registry)

    assert registry.get_command("subagent") is not None
    assert registry.get_command("plan") is not None
    assert registry.get_command("mcp") is not None
    assert registry.get_command("skill") is not None
