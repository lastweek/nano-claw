"""Built-in subagent delegation tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.tools import Tool, ToolResult

if TYPE_CHECKING:
    from src.context import Context
    from src.subagents import SubagentManager


class RunSubagentTool(Tool):
    """Schema-only tool used by the parent agent to delegate subtasks."""

    name = "run_subagent"
    description = (
        "Delegate an independent repo subtask to a fresh child agent that works in the same "
        "repository and returns a structured report. Use this when tasks can be split into "
        "separate investigations or edits."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Delegated task for the child agent.",
            },
            "label": {
                "type": "string",
                "description": "Optional short label for the subagent run.",
            },
            "context": {
                "type": "string",
                "description": "Optional extra parent context for the delegated task.",
            },
            "success_criteria": {
                "type": "string",
                "description": "Optional completion contract for the child.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional file hints. The child should still inspect them itself.",
            },
            "output_hint": {
                "type": "string",
                "description": "Optional hint about the desired report format.",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    def __init__(self, subagent_manager: "SubagentManager") -> None:
        self.subagent_manager = subagent_manager

    def execute(self, context: "Context", **kwargs) -> ToolResult:
        """Guard against accidental direct execution outside the agent runtime."""
        return ToolResult(
            success=False,
            error="run_subagent must be executed by the parent agent runtime",
        )
