"""Test MCP client functionality."""

import json
from unittest.mock import Mock

import httpx
import pytest

from src.mcp import (
    DEFAULT_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSION,
    MCPServer,
    PROTOCOL_VERSION_HEADER,
    SESSION_ID_HEADER,
)


def make_json_response(payload=None, *, status_code=200, headers=None):
    """Create a mock JSON HTTP response."""
    response = Mock()
    response.status_code = status_code
    response.headers = headers or {"content-type": "application/json"}

    if payload is None:
        response.content = b""
        response.text = ""
        response.json.side_effect = json.JSONDecodeError("no body", "", 0)
    else:
        response.content = json.dumps(payload).encode()
        response.text = json.dumps(payload)
        response.json.return_value = payload

    return response


def make_initialize_response(protocol_version=DEFAULT_PROTOCOL_VERSION, request_id=1):
    """Create a standard initialize response."""
    return make_json_response(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "deepwiki", "version": "1.0.0"},
            },
        },
        headers={
            "content-type": "application/json",
            SESSION_ID_HEADER: "session-123",
        },
    )


def make_initialized_notification_response():
    """Create a response for notifications/initialized."""
    return make_json_response(status_code=202)


def test_mcp_server_init():
    """Test MCPServer initialization."""
    server = MCPServer(name="test-server", url="http://localhost:3000")

    assert server.name == "test-server"
    assert server.url == "http://localhost:3000"
    assert server.timeout == 30
    assert server.protocol_version == DEFAULT_PROTOCOL_VERSION


def test_mcp_server_url_trailing_slash():
    """Test that trailing slash is removed from URL."""
    server = MCPServer(name="test-server", url="http://localhost:3000/")

    assert server.url == "http://localhost:3000"


def test_mcp_server_custom_timeout():
    """Test MCPServer with custom timeout."""
    server = MCPServer(name="test-server", url="http://localhost:3000", timeout=60)

    assert server.timeout == 60


def test_list_tools_success():
    """Test successful tool listing with MCP initialization."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo back input",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "message": {"type": "string"}
                                },
                                "required": ["message"],
                            },
                        }
                    ]
                },
            }
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    tools = server.list_tools()

    assert len(tools) == 1
    assert tools[0]["name"] == "echo"
    assert tools[0]["description"] == "Echo back input"
    assert server.session_id == "session-123"
    assert server.protocol_version == DEFAULT_PROTOCOL_VERSION
    assert mock_client.post.call_count == 3

    initialize_call = mock_client.post.call_args_list[0]
    assert initialize_call[0][0] == "http://localhost:3000"
    assert initialize_call[1]["json"]["method"] == "initialize"
    assert initialize_call[1]["json"]["params"]["protocolVersion"] == DEFAULT_PROTOCOL_VERSION
    assert SESSION_ID_HEADER not in initialize_call[1]["headers"]

    notify_call = mock_client.post.call_args_list[1]
    assert notify_call[1]["json"]["method"] == "notifications/initialized"
    assert "id" not in notify_call[1]["json"]
    assert notify_call[1]["headers"][SESSION_ID_HEADER] == "session-123"
    assert notify_call[1]["headers"][PROTOCOL_VERSION_HEADER] == DEFAULT_PROTOCOL_VERSION

    list_call = mock_client.post.call_args_list[2]
    assert list_call[1]["json"]["method"] == "tools/list"
    assert list_call[1]["headers"][SESSION_ID_HEADER] == "session-123"
    assert list_call[1]["headers"][PROTOCOL_VERSION_HEADER] == DEFAULT_PROTOCOL_VERSION


def test_list_tools_empty():
    """Test tool listing with no tools."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        make_json_response(
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    tools = server.list_tools()

    assert tools == []


def test_list_tools_paginates():
    """Test tool listing pagination via nextCursor."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [{"name": "first"}],
                    "nextCursor": "cursor-2",
                },
            }
        ),
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {
                    "tools": [{"name": "second"}],
                },
            }
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    tools = server.list_tools()

    assert [tool["name"] for tool in tools] == ["first", "second"]
    second_page_call = mock_client.post.call_args_list[3]
    assert second_page_call[1]["json"]["params"] == {"cursor": "cursor-2"}


def test_list_tools_supports_sse_response():
    """Test tool listing when the server replies with SSE."""
    mock_client = Mock()
    sse_response = make_json_response(
        status_code=200,
        headers={"content-type": "text/event-stream"},
    )
    sse_response.content = b'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"echo"}]}}\n\n'
    sse_response.text = 'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"echo"}]}}\n\n'

    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        sse_response,
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    tools = server.list_tools()

    assert tools == [{"name": "echo"}]


def test_list_tools_timeout():
    """Test tool listing timeout."""
    mock_client = Mock()
    mock_client.post.side_effect = httpx.TimeoutException("Request timed out", request=None)

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)

    with pytest.raises(TimeoutError, match="timed out after 30s"):
        server.list_tools()


def test_list_tools_http_error():
    """Test tool listing with HTTP error."""
    mock_client = Mock()
    mock_request = Mock()
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    error = httpx.HTTPStatusError("Server error", request=mock_request, response=mock_response)
    mock_client.post.side_effect = error

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)

    with pytest.raises(ConnectionError, match="HTTP 500"):
        server.list_tools()


def test_list_tools_jsonrpc_error():
    """Test tool listing with JSON-RPC error."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "error": {
                    "code": -32600,
                    "message": "Invalid Request",
                },
            }
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)

    with pytest.raises(ConnectionError, match="Invalid Request"):
        server.list_tools()


def test_initialize_falls_back_to_legacy_protocol():
    """Test initialize fallback when the server rejects the latest protocol."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32602,
                    "message": "Unsupported protocol version",
                },
            }
        ),
        make_initialize_response(protocol_version=LEGACY_PROTOCOL_VERSION, request_id=2),
        make_initialized_notification_response(),
        make_json_response(
            {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    assert server.list_tools() == []
    assert server.protocol_version == LEGACY_PROTOCOL_VERSION


def test_call_tool_success():
    """Test successful tool execution."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "content": [{"type": "text", "text": "echo: hello"}],
                },
            }
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    result = server.call_tool("echo", {"message": "hello"})

    assert result == {"data": "echo: hello"}
    call_args = mock_client.post.call_args_list[2]
    assert call_args[0][0] == "http://localhost:3000"
    assert call_args[1]["json"]["method"] == "tools/call"
    assert call_args[1]["json"]["params"]["name"] == "echo"
    assert call_args[1]["headers"][SESSION_ID_HEADER] == "session-123"


def test_call_tool_structured_content():
    """Test tool execution returning structuredContent."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "structuredContent": {"count": 1},
                },
            }
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    result = server.call_tool("count", {})

    assert result == {"data": {"count": 1}}


def test_call_tool_timeout():
    """Test tool execution timeout."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        httpx.TimeoutException("Request timed out", request=None),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client, debug=True)
    result = server.call_tool("echo", {"message": "hello"})

    assert "error" in result
    assert "timed out" in result["error"]


def test_call_tool_http_error():
    """Test tool execution with HTTP error."""
    mock_client = Mock()
    mock_request = Mock()
    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.text = "Tool not found"

    error = httpx.HTTPStatusError("Not found", request=mock_request, response=mock_response)
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        error,
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client, debug=True)
    result = server.call_tool("unknown", {})

    assert "error" in result
    assert "HTTP 404" in result["error"]


def test_call_tool_jsonrpc_error():
    """Test tool execution with JSON-RPC error."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                },
            }
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    result = server.call_tool("unknown", {})

    assert "error" in result
    assert "Method not found" in result["error"]


def test_call_tool_is_error_result():
    """Test tool execution when MCP returns isError."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
        make_json_response(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "Bad query"}],
                },
            }
        ),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    result = server.call_tool("query", {})

    assert result == {"error": "Bad query"}


def test_health_check_success():
    """Test successful health check via MCP initialize."""
    mock_client = Mock()
    mock_client.post.side_effect = [
        make_initialize_response(),
        make_initialized_notification_response(),
    ]

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    assert server.health_check() is True


def test_health_check_failure():
    """Test failed health check."""
    mock_client = Mock()
    mock_client.post.side_effect = Exception("Connection failed")

    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    assert server.health_check() is False


def test_close():
    """Test closing MCP server connection without a session."""
    mock_client = Mock()
    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    server.close()

    mock_client.close.assert_called_once()
    mock_client.delete.assert_not_called()


def test_close_with_session():
    """Test closing MCP server sends DELETE when a session exists."""
    mock_client = Mock()
    server = MCPServer(name="test", url="http://localhost:3000", client=mock_client)
    server.session_id = "session-123"
    server.protocol_version = DEFAULT_PROTOCOL_VERSION
    server._initialized = True

    server.close()

    mock_client.delete.assert_called_once()
    delete_call = mock_client.delete.call_args
    assert delete_call[0][0] == "http://localhost:3000"
    assert delete_call[1]["headers"][SESSION_ID_HEADER] == "session-123"
    assert delete_call[1]["headers"][PROTOCOL_VERSION_HEADER] == DEFAULT_PROTOCOL_VERSION
    mock_client.close.assert_called_once()


def test_context_manager():
    """Test using MCPServer as context manager."""
    mock_client = Mock()

    with MCPServer(name="test", url="http://localhost:3000", client=mock_client) as server:
        assert server.name == "test"

    mock_client.close.assert_called_once()
