"""Tests for help and tool slash commands."""

from types import SimpleNamespace

from rich.console import Console

from src.commands.help_cmds import register_help_commands
from src.commands.registry import CommandRegistry
from src.tools import ToolRegistry
from src.tools.read import ReadTool
from src.tools.write import WriteTool


def normalize_output(output: str) -> str:
    """Normalize Rich exported text for stable assertions."""
    return "\n".join(line.rstrip() for line in output.splitlines())


def test_tool_command_lists_only_active_tools():
    """`/tool` should keep listing the active registry without debug skip reasons."""
    registry = CommandRegistry()
    register_help_commands(registry)

    tool_registry = ToolRegistry()
    tool_registry.register(ReadTool())
    tool_registry.register(WriteTool())

    console = Console(record=True, force_terminal=False, width=100)
    agent = SimpleNamespace(tools=tool_registry)

    registry.execute("/tool", console, {"agent": agent})

    normalized = normalize_output(console.export_text())
    assert "Available Tools (2)" in normalized
    assert "read" in normalized
    assert "write" in normalized
    assert "skipped:" not in normalized
    assert "finder_action" not in normalized
