"""Built-in plan-writing tool for planning mode."""

from __future__ import annotations

from pathlib import Path

from src.plan_mode import write_plan_content
from src.tools import Tool, ToolResult


class WritePlanTool(Tool):
    """Write the canonical plan artifact for the current session."""

    name = "write_plan"
    description = (
        "Write or replace the canonical session plan artifact for planning mode. "
        "This tool can write only the current session plan file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Full Markdown content for the canonical plan file.",
            }
        },
        "required": ["content"],
        "additionalProperties": False,
    }

    def execute(self, context, **kwargs) -> ToolResult:
        """Write the canonical session plan file."""
        try:
            current_plan = context.get_current_plan()
            if current_plan is None:
                return ToolResult(success=False, error="No active session plan")

            content = self._require_param(kwargs, "content")
            updated_plan = write_plan_content(context, content)
            plan_path = Path(updated_plan.file_path)
            return ToolResult(success=True, data=f"Plan written: {plan_path}")
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:
            return ToolResult(success=False, error=f"Error writing plan: {exc}")
