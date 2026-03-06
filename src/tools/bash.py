"""Bash tool for nano-claw."""

import subprocess
from src.tools import Tool, ToolResult


class BashTool(Tool):
    """Tool for executing shell commands."""

    name = "run_command"
    description = "Execute a shell command in the current working directory. Use this to run tests, install packages, use git, etc."
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute"
            }
        },
        "required": ["command"]
    }

    DEFAULT_TIMEOUT = 30

    def execute(self, context, **kwargs) -> ToolResult:
        """Execute a shell command."""
        try:
            command = self._require_param(kwargs, "command")

            result = subprocess.run(
                command,
                shell=True,
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=self.DEFAULT_TIMEOUT
            )

            output = []
            if result.stdout:
                output.append(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                output.append(f"STDERR:\n{result.stderr}")

            output_text = "\n".join(output) if output else "(no output)"

            if result.returncode == 0:
                return ToolResult(success=True, data=output_text)
            else:
                return ToolResult(
                    success=False,
                    error=f"Exit code: {result.returncode}\n{output_text}"
                )

        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"Command timed out after {self.DEFAULT_TIMEOUT} seconds"
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Error executing command: {e}")
