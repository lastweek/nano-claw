"""Control-plane runtime refresh tool."""

from __future__ import annotations

from collections.abc import Callable

from src.tools import Tool, ToolProfile, ToolResult, ToolRegistry


class RefreshRuntimeCapabilitiesTool(Tool):
    """Reload config-backed skills, extensions, MCP, and tool availability."""

    name = "refresh_runtime_capabilities"
    description = (
        "Reload config-backed tools and skills for the current session without restarting. "
        "Use this after new extensions, skills, or MCP/tool settings become available."
    )
    parameters = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Optional short note explaining why the runtime is being refreshed.",
            }
        },
        "additionalProperties": False,
    }

    def __init__(self, refresh_callback: Callable[[str | None], dict]) -> None:
        self._refresh_callback = refresh_callback

    def execute(self, context, **kwargs) -> ToolResult:
        reason = kwargs.get("reason")
        if reason is not None and not isinstance(reason, str):
            return ToolResult(success=False, error="reason must be a string")
        try:
            return ToolResult(success=True, data=self._refresh_callback(reason))
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


def register_runtime_refresh_tool(
    registry: ToolRegistry,
    *,
    tool_profile: ToolProfile,
    refresh_callback: Callable[[str | None], dict] | None,
) -> None:
    """Attach the runtime-refresh tool when the current profile allows it."""
    if refresh_callback is None:
        return
    if tool_profile not in {ToolProfile.BUILD, ToolProfile.PLAN_MAIN}:
        return
    registry.register(RefreshRuntimeCapabilitiesTool(refresh_callback))
