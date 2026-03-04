"""Test BashTool."""

import pytest
from src.tools.bash import BashTool
from src.context import Context


class TestBashTool:
    """Test BashTool functionality."""

    def test_run_simple_command(self, test_context):
        """Test running a simple command."""
        tool = BashTool()
        result = tool.execute(test_context, command="echo hello")
        assert result.success is True
        assert "hello" in result.data

    def test_run_command_fails(self, test_context):
        """Test running a command that fails."""
        tool = BashTool()
        result = tool.execute(test_context, command="ls /nonexistent")
        assert result.success is False
        assert "Exit code" in result.error

    def test_run_missing_command_param(self, test_context):
        """Test running without command parameter."""
        tool = BashTool()
        result = tool.execute(test_context)
        assert result.success is False
        assert "command is required" in result.error

    def test_run_command_in_cwd(self, test_context, temp_dir):
        """Test that command runs in the context cwd."""
        # Create a file in temp dir
        (temp_dir / "test_marker.txt").write_text("test")
        tool = BashTool()
        result = tool.execute(test_context, command="ls test_marker.txt")
        assert result.success is True
        assert "test_marker.txt" in result.data

    def test_tool_metadata(self):
        """Test tool has correct metadata."""
        tool = BashTool()
        assert tool.name == "run_command"
        assert "command" in tool.description.lower()
        assert "command" in tool.parameters["required"]
        assert tool.DEFAULT_TIMEOUT == 30
