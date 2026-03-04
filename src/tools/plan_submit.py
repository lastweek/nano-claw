"""Built-in plan-submission tool for planning mode."""

from __future__ import annotations

from src.plan_mode import mark_plan_ready_for_review
from src.tools import Tool, ToolResult


class SubmitPlanTool(Tool):
    """Finalize a planning turn and submit the plan for user review."""

    name = "submit_plan"
    description = (
        "Submit the current session plan for user review. "
        "Use this only after the canonical plan file has been written."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Short summary of the proposed plan.",
            },
            "report": {
                "type": "string",
                "description": "Human-facing planning report to show before approval.",
            },
        },
        "required": ["summary", "report"],
        "additionalProperties": False,
    }

    def execute(self, context, **kwargs) -> ToolResult:
        """Store plan review metadata and mark the plan ready for review."""
        try:
            current_plan = context.get_current_plan()
            if current_plan is None:
                return ToolResult(success=False, error="No active session plan")
            if not current_plan.content.strip():
                return ToolResult(
                    success=False,
                    error="The canonical plan file is empty. Write the plan before submitting it.",
                )

            summary = self._require_param(kwargs, "summary")
            report = self._require_param(kwargs, "report")
            updated_plan = mark_plan_ready_for_review(
                context,
                summary=summary,
                report=report,
            )
            return ToolResult(
                success=True,
                data={
                    "plan_id": updated_plan.plan_id,
                    "file_path": updated_plan.file_path,
                    "summary": updated_plan.summary,
                    "report": updated_plan.report,
                },
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:
            return ToolResult(success=False, error=f"Error submitting plan: {exc}")
