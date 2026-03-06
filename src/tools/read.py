"""Read file tool for nano-claw."""

from src.tools import Tool, ToolResult


class ReadTool(Tool):
    """Tool for reading file contents."""

    name = "read_file"
    description = "Read the contents of a file from the filesystem. Use this to understand code and file contents."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file (relative to current working directory or absolute)"
            }
        },
        "required": ["file_path"]
    }

    def execute(self, context, **kwargs) -> ToolResult:
        """Read a file and return its contents with line numbers."""
        try:
            file_path = self._require_param(kwargs, "file_path")
            path = self._resolve_path(context, file_path)

            content = path.read_text()
            # Add line numbers
            lines = content.splitlines()
            numbered = "\n".join(f"{i+1:4d}    {line}" for i, line in enumerate(lines))
            return ToolResult(success=True, data=numbered)

        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except FileNotFoundError:
            return ToolResult(success=False, error=f"File not found: {path}")
        except IsADirectoryError:
            return ToolResult(success=False, error=f"Path is a directory, not a file: {path}")
        except PermissionError:
            return ToolResult(success=False, error=f"Permission denied reading: {path}")
        except Exception as e:
            return ToolResult(success=False, error=f"Error reading file: {e}")
