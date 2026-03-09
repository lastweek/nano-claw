"""Tests for /runtime and /extension slash commands."""

from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from src.commands import builtin
from src.commands.registry import CommandRegistry


def create_console(buffer: io.StringIO) -> Console:
    """Create a deterministic Rich console."""
    return Console(file=buffer, force_terminal=False, color_system=None, width=120)


def create_command_registry() -> CommandRegistry:
    """Create a registry with all builtin commands."""
    registry = CommandRegistry()
    builtin.register_all(registry)
    return registry


def test_runtime_reload_command_prints_refresh_diff():
    """`/runtime reload` should call the refresh callback and render the diff."""
    registry = create_command_registry()
    output = io.StringIO()
    console = create_console(output)
    command_context = {
        "runtime_refresh_callback": lambda reason: {
            "tool_profile": "build",
            "added_tools": ["sample_extension_tool"],
            "removed_tools": [],
            "added_skills": ["sample-extension-skill"],
            "removed_skills": [],
            "pruned_skills": [{"name": "legacy", "reason": "missing"}],
            "warnings": ["duplicate extension"],
        }
    }

    registry.execute("/runtime reload", console, command_context)

    text = output.getvalue()
    assert "Runtime Reload" in text
    assert "sample_extension_tool" in text
    assert "sample-extension-skill" in text
    assert "legacy (missing)" in text
    assert "duplicate extension" in text


def test_extension_commands_list_show_install_and_reload():
    """`/extension` should expose list, show, install, and reload flows."""
    registry = create_command_registry()
    extension = SimpleNamespace(
        name="sample-extension",
        version="1.2.3",
        description="Sample extension",
        install_scope="repo",
        root_dir="/tmp/repo/.babyclaw/extensions/sample-extension",
        manifest_file="/tmp/repo/.babyclaw/extensions/sample-extension/EXTENSION.yaml",
        command=("python3", "runner.py"),
        skill_root="/tmp/repo/.babyclaw/extensions/sample-extension/skills",
        tool_specs=(SimpleNamespace(name="sample_extension_tool", description="Extension tool"),),
    )

    class FakeExtensionManager:
        def __init__(self) -> None:
            self.installed: list[str] = []

        def list_extensions(self):
            return [extension]

        def get_extension(self, name: str):
            if name == extension.name:
                return extension
            return None

        def install_from_catalog(self, package_ref: str):
            self.installed.append(package_ref)
            return SimpleNamespace(extension=extension)

    manager = FakeExtensionManager()
    output = io.StringIO()
    console = create_console(output)
    command_context = {
        "extension_manager": manager,
        "runtime_refresh_callback": lambda reason: {
            "added_tools": ["sample_extension_tool"],
            "added_skills": ["sample-extension-skill"],
        },
    }

    registry.execute("/extension", console, command_context)
    registry.execute("/extension show sample-extension", console, command_context)
    registry.execute("/extension install curated:sample-extension", console, command_context)
    registry.execute("/extension reload", console, command_context)

    text = output.getvalue()
    assert "Extensions (1)" in text
    assert "Extension: sample-extension" in text
    assert "Installed extension:" in text
    assert "Run /runtime reload to activate" in text
    assert "Extension Reload" in text
    assert manager.installed == ["curated:sample-extension"]
