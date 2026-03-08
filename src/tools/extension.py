"""Out-of-process runtime tools provided by extension bundles."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from src.extensions import ExtensionSpec, ExtensionToolSpec
from src.tools import Tool, ToolResult


class ExtensionTool(Tool):
    """Invoke one extension-defined tool via the bundle runner command."""

    tool_source = "extension"

    def __init__(
        self,
        extension: ExtensionSpec,
        tool_spec: ExtensionToolSpec,
        *,
        timeout_seconds: int,
    ) -> None:
        self.extension = extension
        self.extension_name = extension.name
        self.extension_version = extension.version
        self.name = tool_spec.name
        self.description = tool_spec.description
        self.parameters = tool_spec.parameters
        self._timeout_seconds = timeout_seconds

    def execute(self, context, **kwargs) -> ToolResult:
        payload = {
            "tool": self.name,
            "arguments": kwargs,
            "cwd": str(context.cwd),
            "session_id": context.session_id,
        }
        try:
            completed = subprocess.run(
                list(self.extension.command),
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                cwd=self.extension.root_dir,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=(
                    f"Extension tool '{self.name}' timed out after "
                    f"{self._timeout_seconds}s"
                ),
            )
        except OSError as exc:
            return ToolResult(success=False, error=f"Failed to launch extension tool '{self.name}': {exc}")

        stderr_text = (completed.stderr or "").strip()
        if completed.returncode != 0:
            error_text = stderr_text or f"Extension tool exited with status {completed.returncode}"
            return ToolResult(success=False, error=error_text)

        stdout_text = (completed.stdout or "").strip()
        if not stdout_text:
            return ToolResult(success=False, error=f"Extension tool '{self.name}' returned no JSON output")

        try:
            result_payload = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            return ToolResult(success=False, error=f"Extension tool '{self.name}' returned invalid JSON: {exc}")

        if not isinstance(result_payload, dict):
            return ToolResult(success=False, error=f"Extension tool '{self.name}' returned a non-object result")

        success = result_payload.get("success")
        if not isinstance(success, bool):
            return ToolResult(
                success=False,
                error=f"Extension tool '{self.name}' result must include boolean 'success'",
            )

        if success:
            return ToolResult(success=True, data=result_payload.get("data"))

        error_text = result_payload.get("error")
        if not isinstance(error_text, str) or not error_text.strip():
            return ToolResult(
                success=False,
                error=f"Extension tool '{self.name}' failed without an error message",
            )
        return ToolResult(success=False, error=error_text.strip())
