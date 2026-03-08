"""Tests for /capability slash commands."""

from __future__ import annotations

import io

from rich.console import Console

from src.capabilities import CapabilityRequestManager
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


def test_capability_commands_list_show_dismiss_and_resolve():
    """`/capability` should expose list, show, dismiss, and resolve flows."""
    registry = create_command_registry()
    manager = CapabilityRequestManager()
    request = manager.create_or_update(
        summary="Need GitHub tools",
        reason="This task needs GitHub API access.",
        desired_capability="github issue tools",
        request_type="install_extension",
        package_ref="curated:github",
        extension_name="github",
    )

    output = io.StringIO()
    console = create_console(output)
    context = {"capability_request_manager": manager}

    registry.execute("/capability", console, context)
    registry.execute(f"/capability show {request.request_id}", console, context)
    registry.execute(f"/capability dismiss {request.request_id}", console, context)
    registry.execute(f"/capability resolve {request.request_id}", console, context)

    text = output.getvalue()
    assert "Capability Requests (1)" in text
    assert f"Capability Request: {request.request_id}" in text
    assert "github issue tools" in text
    assert "/extension install curated:github" in text
    assert manager.get_request(request.request_id).status == "resolved"
