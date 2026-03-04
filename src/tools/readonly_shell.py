"""Read-only shell/search tool for planning mode."""

from __future__ import annotations

import subprocess

from src.tools import Tool, ToolResult


_ALLOWED_COMMANDS = {"rg", "ls", "find", "git"}
_ALLOWED_GIT_SUBCOMMANDS = {"status", "diff", "grep", "show", "log"}


class ReadOnlyShellTool(Tool):
    """Execute an allowlisted read-only command in planning mode."""

    name = "run_readonly_command"
    description = (
        "Run a read-only repository inspection command without using a shell. "
        "Use argv form only. Intended for planning mode searches and inspection."
    )
    parameters = {
        "type": "object",
        "properties": {
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command argv to run without shell expansion.",
            }
        },
        "required": ["argv"],
        "additionalProperties": False,
    }

    DEFAULT_TIMEOUT = 30

    def execute(self, context, **kwargs) -> ToolResult:
        """Run one allowlisted read-only command."""
        try:
            argv = kwargs.get("argv")
            if not isinstance(argv, list) or not argv:
                raise ValueError("argv is required and must be a non-empty list")

            normalized_argv = [str(item) for item in argv if str(item).strip()]
            if not normalized_argv:
                raise ValueError("argv is required and must be a non-empty list")

            command = normalized_argv[0]
            if command not in _ALLOWED_COMMANDS:
                raise ValueError(f"Command not allowed in planning mode: {command}")

            if command == "git":
                if len(normalized_argv) < 2:
                    raise ValueError("git requires a read-only subcommand")
                subcommand = normalized_argv[1]
                if subcommand not in _ALLOWED_GIT_SUBCOMMANDS:
                    raise ValueError(f"Git subcommand not allowed in planning mode: {subcommand}")

            result = subprocess.run(
                normalized_argv,
                cwd=context.cwd,
                capture_output=True,
                text=True,
                timeout=self.DEFAULT_TIMEOUT,
                shell=False,
            )

            output_parts = []
            if result.stdout:
                output_parts.append(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                output_parts.append(f"STDERR:\n{result.stderr}")
            output_text = "\n".join(output_parts) if output_parts else "(no output)"

            if result.returncode == 0:
                return ToolResult(success=True, data=output_text)
            return ToolResult(
                success=False,
                error=f"Exit code: {result.returncode}\n{output_text}",
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"Command timed out after {self.DEFAULT_TIMEOUT} seconds",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"Error executing read-only command: {exc}")
