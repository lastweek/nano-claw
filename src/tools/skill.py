"""Built-in skill-related tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.tools import Tool, ToolResult

if TYPE_CHECKING:
    from src.skills import SkillManager


class LoadSkillTool(Tool):
    """Tool for loading a skill's full instructions into the current turn."""

    name = "load_skill"
    description = (
        "Load a discovered skill's full instructions and bundled resource inventory "
        "for the current task."
    )
    parameters = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Name of the skill to load",
            }
        },
        "required": ["skill_name"],
        "additionalProperties": False,
    }

    def __init__(self, skill_manager: "SkillManager") -> None:
        self.skill_manager = skill_manager

    def execute(self, context, **kwargs) -> ToolResult:
        """Return the formatted skill instructions for the requested skill."""
        skill_name = self._require_param(kwargs, "skill_name")

        try:
            payload = self.skill_manager.format_skill_for_tool(skill_name)
        except KeyError:
            return ToolResult(success=False, error=f"Unknown skill: {skill_name}")

        return ToolResult(success=True, data=payload)
