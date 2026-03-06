"""Write file tool for nano-claw."""

from src.tools import Tool, ToolResult


class WriteTool(Tool):
    """Tool for writing/creating files."""

    name = "write_file"
    description = "Create a new file or completely overwrite an existing file with new content. Creates parent directories if needed."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file (relative to current working directory or absolute)"
            },
            "content": {
                "type": "string",
                "description": "Full file content to write"
            }
        },
        "required": ["file_path", "content"]
    }

    def execute(self, context, **kwargs) -> ToolResult:
        """Write content to a file."""
        try:
            file_path = self._require_param(kwargs, "file_path")
            content = self._require_param(kwargs, "content")
            path = self._resolve_path(context, file_path)

            # Create parent directories if needed
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write content
            path.write_text(content)

            return ToolResult(success=True, data=f"File written: {path}")

        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except PermissionError:
            return ToolResult(success=False, error=f"Permission denied writing: {path}")
        except Exception as e:
            return ToolResult(success=False, error=f"Error writing file: {e}")
