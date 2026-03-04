"""Test Tool base class and ToolRegistry."""

import pytest
from src.tools import Tool, ToolResult, ToolRegistry
from src.context import Context
from pathlib import Path


class DummyTool(Tool):
    """Dummy tool for testing."""

    name = "dummy"
    description = "A dummy tool"
    parameters = {"type": "object"}

    def execute(self, context, **kwargs):
        return ToolResult(success=True, data="dummy executed")


class TestToolResult:
    """Test ToolResult dataclass."""

    def test_success_result(self):
        """Test creating a successful result."""
        result = ToolResult(success=True, data="test data")
        assert result.success is True
        assert result.data == "test data"
        assert result.error is None

    def test_error_result(self):
        """Test creating an error result."""
        result = ToolResult(success=False, error="test error")
        assert result.success is False
        assert result.error == "test error"
        assert result.data is None


class TestTool:
    """Test Tool base class."""

    def test_require_param_success(self, test_context):
        """Test _require_param with valid parameter."""
        tool = DummyTool()
        value = tool._require_param({"file_path": "test.txt"}, "file_path")
        assert value == "test.txt"

    def test_require_param_missing(self, test_context):
        """Test _require_param with missing parameter."""
        tool = DummyTool()
        with pytest.raises(ValueError, match="file_path is required"):
            tool._require_param({}, "file_path")

    def test_require_param_empty(self, test_context):
        """Test _require_param with empty parameter."""
        tool = DummyTool()
        with pytest.raises(ValueError, match="file_path is required"):
            tool._require_param({"file_path": ""}, "file_path")

    def test_resolve_path(self, test_context):
        """Test _resolve_path resolves relative to cwd."""
        tool = DummyTool()
        path = tool._resolve_path(test_context, "test.txt")
        assert path == test_context.cwd / "test.txt"

    def test_to_schema(self):
        """Test to_schema returns correct format."""
        tool = DummyTool()
        schema = tool.to_schema()
        assert schema == {
            "type": "function",
            "function": {
                "name": "dummy",
                "description": "A dummy tool",
                "parameters": {"type": "object"},
            },
        }


class TestToolRegistry:
    """Test ToolRegistry."""

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        tool = DummyTool()
        registry.register(tool)
        assert "dummy" in registry._tools

    def test_get_tool(self):
        """Test getting a registered tool."""
        registry = ToolRegistry()
        tool = DummyTool()
        registry.register(tool)
        retrieved = registry.get("dummy")
        assert retrieved is tool

    def test_get_nonexistent_tool(self):
        """Test getting a non-existent tool returns None."""
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_get_tool_schemas(self):
        """Test getting all tool schemas."""
        registry = ToolRegistry()
        tool = DummyTool()
        registry.register(tool)
        schemas = registry.get_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "dummy"

    def test_list_tools(self):
        """Test listing tool names."""
        registry = ToolRegistry()
        tool = DummyTool()
        registry.register(tool)
        names = registry.list_tools()
        assert names == ["dummy"]
