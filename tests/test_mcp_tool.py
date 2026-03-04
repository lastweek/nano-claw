"""Test MCP tool adapter functionality."""

from unittest.mock import Mock

from src.mcp import MCPTool


def test_mcp_tool_name():
    """Test tool name with server prefix."""
    mock_server = Mock()
    mock_server.name = "database"

    tool_def = {"name": "query_users"}
    tool = MCPTool(mock_server, tool_def)

    assert tool.name == "database:query_users"


def test_mcp_tool_description():
    """Test tool description."""
    mock_server = Mock()
    mock_server.name = "database"

    tool_def = {
        "name": "query_users",
        "description": "Query users from database",
    }
    tool = MCPTool(mock_server, tool_def)

    assert tool.description == "Query users from database (via database)"


def test_mcp_tool_description_missing():
    """Test tool description when not provided."""
    mock_server = Mock()
    mock_server.name = "database"

    tool_def = {"name": "query_users"}
    tool = MCPTool(mock_server, tool_def)

    assert tool.description == "Tool from database"


def test_mcp_tool_parameters_from_input_schema():
    """Test JSON schema passthrough for MCP inputSchema."""
    mock_server = Mock()
    mock_server.name = "database"

    tool_def = {
        "name": "query_users",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max results",
                }
            },
            "required": ["limit"],
        },
    }
    tool = MCPTool(mock_server, tool_def)

    assert tool.parameters == {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max results",
            }
        },
        "required": ["limit"],
    }


def test_mcp_tool_parameters_legacy_format():
    """Test legacy parameter conversion for older MCP servers."""
    mock_server = Mock()
    mock_server.name = "database"

    tool_def = {
        "name": "query",
        "parameters": [
            {
                "name": "limit",
                "type": "integer",
                "description": "Max results",
                "required": True,
            },
            {
                "name": "offset",
                "type": "integer",
                "description": "Start offset",
                "required": False,
            },
        ],
    }
    tool = MCPTool(mock_server, tool_def)

    assert tool.parameters == {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max results",
            },
            "offset": {
                "type": "integer",
                "description": "Start offset",
            },
        },
        "required": ["limit"],
    }


def test_mcp_tool_parameters_empty():
    """Test tool parameters when none provided."""
    mock_server = Mock()
    mock_server.name = "database"

    tool_def = {"name": "query_users"}
    tool = MCPTool(mock_server, tool_def)

    assert tool.parameters == {"type": "object", "properties": {}}


def test_execute_success():
    """Test successful tool execution."""
    mock_server = Mock()
    mock_server.name = "database"
    mock_server.call_tool.return_value = {"data": [{"id": 1, "name": "Alice"}]}

    tool_def = {"name": "query_users"}
    tool = MCPTool(mock_server, tool_def)

    context = Mock()
    result = tool.execute(context, limit=10)

    assert result.success is True
    assert result.data == [{"id": 1, "name": "Alice"}]
    assert result.error is None
    mock_server.call_tool.assert_called_once_with("query_users", {"limit": 10})


def test_execute_error():
    """Test tool execution with error."""
    mock_server = Mock()
    mock_server.name = "database"
    mock_server.call_tool.return_value = {"error": "Invalid query"}

    tool_def = {"name": "query_users"}
    tool = MCPTool(mock_server, tool_def)

    context = Mock()
    result = tool.execute(context, limit=10)

    assert result.success is False
    assert result.error == "Invalid query"
    assert result.data is None


def test_execute_timeout():
    """Test tool execution timeout."""
    mock_server = Mock()
    mock_server.name = "database"
    mock_server.call_tool.side_effect = TimeoutError("Request timed out")

    tool_def = {"name": "query_users"}
    tool = MCPTool(mock_server, tool_def)

    context = Mock()
    result = tool.execute(context, limit=10)

    assert result.success is False
    assert "timed out" in result.error.lower()


def test_execute_connection_error():
    """Test tool execution with connection error."""
    mock_server = Mock()
    mock_server.name = "database"
    mock_server.call_tool.side_effect = ConnectionError("Connection failed")

    tool_def = {"name": "query_users"}
    tool = MCPTool(mock_server, tool_def)

    context = Mock()
    result = tool.execute(context, limit=10)

    assert result.success is False
    assert "connection failed" in result.error.lower()


def test_execute_generic_exception():
    """Test tool execution with generic exception."""
    mock_server = Mock()
    mock_server.name = "database"
    mock_server.call_tool.side_effect = ValueError("Unexpected error")

    tool_def = {"name": "query_users"}
    tool = MCPTool(mock_server, tool_def)

    context = Mock()
    result = tool.execute(context, limit=10)

    assert result.success is False
    assert "unexpected error" in result.error.lower()


def test_to_schema():
    """Test converting to OpenAI function schema."""
    mock_server = Mock()
    mock_server.name = "database"

    tool_def = {
        "name": "query_users",
        "description": "Query users",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max results",
                }
            },
            "required": ["limit"],
        },
    }
    tool = MCPTool(mock_server, tool_def)

    schema = tool.to_schema()

    assert schema == {
        "type": "function",
        "function": {
            "name": "database:query_users",
            "description": "Query users (via database)",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max results",
                    }
                },
                "required": ["limit"],
            },
        },
    }


def test_to_schema_no_parameters():
    """Test converting to schema with no parameters."""
    mock_server = Mock()
    mock_server.name = "database"

    tool_def = {"name": "ping", "description": "Ping server"}
    tool = MCPTool(mock_server, tool_def)

    schema = tool.to_schema()

    assert schema["function"]["name"] == "database:ping"
    assert schema["function"]["parameters"] == {"type": "object", "properties": {}}
