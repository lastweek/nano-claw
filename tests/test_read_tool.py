"""Test ReadTool."""

import pytest
from src.tools.read import ReadTool
from src.context import Context


class TestReadTool:
    """Test ReadTool functionality."""

    def test_read_file_success(self, test_context, sample_file):
        """Test reading an existing file."""
        tool = ReadTool()
        result = tool.execute(test_context, file_path="test.txt")
        assert result.success is True
        assert "   1    Hello" in result.data
        assert "   2    World" in result.data
        assert "   3    Test" in result.data

    def test_read_file_missing_param(self, test_context):
        """Test reading without file_path parameter."""
        tool = ReadTool()
        result = tool.execute(test_context)
        assert result.success is False
        assert "file_path is required" in result.error

    def test_read_file_not_found(self, test_context):
        """Test reading a non-existent file."""
        tool = ReadTool()
        result = tool.execute(test_context, file_path="nonexistent.txt")
        assert result.success is False
        assert "File not found" in result.error

    def test_read_directory(self, test_context, temp_dir):
        """Test reading a directory as file."""
        tool = ReadTool()
        result = tool.execute(test_context, file_path=str(temp_dir))
        assert result.success is False
        assert "directory" in result.error.lower()

    def test_tool_metadata(self):
        """Test tool has correct metadata."""
        tool = ReadTool()
        assert tool.name == "read_file"
        assert "file" in tool.description.lower()
        assert "file_path" in tool.parameters["properties"]
        assert "file_path" in tool.parameters["required"]
