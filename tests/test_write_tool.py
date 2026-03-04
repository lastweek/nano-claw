"""Test WriteTool."""

import pytest
from src.tools.write import WriteTool
from src.context import Context


class TestWriteTool:
    """Test WriteTool functionality."""

    def test_write_file_new(self, test_context):
        """Test writing a new file."""
        tool = WriteTool()
        result = tool.execute(test_context, file_path="new.txt", content="Hello World")
        assert result.success is True
        assert "File written" in result.data
        # Verify file was created
        assert (test_context.cwd / "new.txt").read_text() == "Hello World"

    def test_write_file_overwrite(self, test_context, sample_file):
        """Test overwriting an existing file."""
        tool = WriteTool()
        result = tool.execute(test_context, file_path="test.txt", content="New content")
        assert result.success is True
        assert (test_context.cwd / "test.txt").read_text() == "New content"

    def test_write_creates_directories(self, test_context):
        """Test that write creates parent directories."""
        tool = WriteTool()
        result = tool.execute(test_context, file_path="subdir/nested/file.txt", content="test")
        assert result.success is True
        assert (test_context.cwd / "subdir" / "nested" / "file.txt").exists()

    def test_write_missing_file_path(self, test_context):
        """Test writing without file_path parameter."""
        tool = WriteTool()
        result = tool.execute(test_context, content="test")
        assert result.success is False
        assert "file_path is required" in result.error

    def test_write_missing_content(self, test_context):
        """Test writing without content parameter."""
        tool = WriteTool()
        result = tool.execute(test_context, file_path="test.txt")
        assert result.success is False
        assert "content is required" in result.error

    def test_tool_metadata(self):
        """Test tool has correct metadata."""
        tool = WriteTool()
        assert tool.name == "write_file"
        assert "create" in tool.description.lower() or "overwrite" in tool.description.lower()
        assert "file_path" in tool.parameters["required"]
        assert "content" in tool.parameters["required"]
