"""Test MCP manager functionality."""

import pytest
from unittest.mock import Mock, patch
from src.mcp import MCPManager
from src.tools import ToolRegistry


def test_mcp_manager_init_empty():
    """Test MCPManager initialization with no servers."""
    manager = MCPManager([])
    assert manager._servers == {}


def test_mcp_manager_init_with_servers():
    """Test MCPManager initialization with servers."""
    configs = [
        {"name": "server1", "url": "http://localhost:3000", "enabled": True, "timeout": 30},
        {"name": "server2", "url": "http://localhost:3001", "enabled": True, "timeout": 60}
    ]

    manager = MCPManager(configs)

    assert len(manager._servers) == 2
    assert "server1" in manager._servers
    assert "server2" in manager._servers
    assert manager._servers["server1"].timeout == 30
    assert manager._servers["server2"].timeout == 60


def test_mcp_manager_init_disabled_servers():
    """Test that disabled servers are not loaded."""
    configs = [
        {"name": "enabled_server", "url": "http://localhost:3000", "enabled": True},
        {"name": "disabled_server", "url": "http://localhost:3001", "enabled": False}
    ]

    manager = MCPManager(configs)

    assert len(manager._servers) == 1
    assert "enabled_server" in manager._servers
    assert "disabled_server" not in manager._servers


def test_mcp_manager_init_default_timeout():
    """Test that default timeout is applied."""
    configs = [
        {"name": "server1", "url": "http://localhost:3000", "enabled": True}
    ]

    manager = MCPManager(configs)

    assert manager._servers["server1"].timeout == 30


def test_register_tools_success():
    """Test successful tool registration."""
    mock_server = Mock()
    mock_server.name = "database"
    mock_server.list_tools.return_value = [
        {"name": "query", "description": "Query database"}
    ]

    with patch('src.mcp.MCPServer', return_value=mock_server):
        manager = MCPManager([{"name": "database", "url": "http://localhost:3000"}])
        registry = ToolRegistry()

        manager.register_tools(registry)

        # Should have registered the MCP tool
        assert "database:query" in registry._tools


def test_register_tools_multiple_servers():
    """Test tool registration from multiple servers."""
    mock_server1 = Mock()
    mock_server1.name = "database"
    mock_server1.list_tools.return_value = [
        {"name": "query", "description": "Query database"}
    ]

    mock_server2 = Mock()
    mock_server2.name = "github"
    mock_server2.list_tools.return_value = [
        {"name": "create_issue", "description": "Create GitHub issue"}
    ]

    with patch('src.mcp.MCPServer', side_effect=[mock_server1, mock_server2]):
        manager = MCPManager([
            {"name": "database", "url": "http://localhost:3000"},
            {"name": "github", "url": "http://localhost:3001"}
        ])
        registry = ToolRegistry()

        manager.register_tools(registry)

        assert "database:query" in registry._tools
        assert "github:create_issue" in registry._tools


def test_register_tools_server_failure():
    """Test that one server failure doesn't prevent others from loading."""
    mock_server1 = Mock()
    mock_server1.name = "working"
    mock_server1.list_tools.return_value = [
        {"name": "tool1", "description": "Working tool"}
    ]

    mock_server2 = Mock()
    mock_server2.name = "broken"
    mock_server2.list_tools.side_effect = Exception("Connection failed")

    with patch('src.mcp.MCPServer', side_effect=[mock_server1, mock_server2]):
        manager = MCPManager([
            {"name": "working", "url": "http://localhost:3000"},
            {"name": "broken", "url": "http://localhost:3001"}
        ])
        registry = ToolRegistry()

        # Should not raise exception
        manager.register_tools(registry)

        # Should have tool from working server
        assert "working:tool1" in registry._tools
        # Should not have tool from broken server
        assert "broken:" not in str(registry._tools.keys())


def test_register_tools_prints_warning(capfd):
    """Test that warning is printed when server fails."""
    mock_server = Mock()
    mock_server.name = "broken"
    mock_server.list_tools.side_effect = Exception("Connection failed")

    with patch('src.mcp.MCPServer', return_value=mock_server):
        manager = MCPManager([{"name": "broken", "url": "http://localhost:3000"}])
        registry = ToolRegistry()

        manager.register_tools(registry)

        captured = capfd.readouterr()
        assert "Warning" in captured.out
        assert "broken" in captured.out


def test_get_server_status():
    """Test getting health status of all servers."""
    mock_server1 = Mock()
    mock_server1.health_check.return_value = True

    mock_server2 = Mock()
    mock_server2.health_check.return_value = False

    with patch('src.mcp.MCPServer', side_effect=[mock_server1, mock_server2]):
        manager = MCPManager([
            {"name": "server1", "url": "http://localhost:3000"},
            {"name": "server2", "url": "http://localhost:3001"}
        ])

        status = manager.get_server_status()

        assert status["server1"] is True
        assert status["server2"] is False


def test_close_all():
    """Test closing all server connections."""
    mock_server1 = Mock()
    mock_server2 = Mock()

    with patch('src.mcp.MCPServer', side_effect=[mock_server1, mock_server2]):
        manager = MCPManager([
            {"name": "server1", "url": "http://localhost:3000"},
            {"name": "server2", "url": "http://localhost:3001"}
        ])

        manager.close_all()

        mock_server1.close.assert_called_once()
        mock_server2.close.assert_called_once()


def test_register_tools_empty_server():
    """Test tool registration when server has no tools."""
    mock_server = Mock()
    mock_server.name = "empty"
    mock_server.list_tools.return_value = []

    with patch('src.mcp.MCPServer', return_value=mock_server):
        manager = MCPManager([{"name": "empty", "url": "http://localhost:3000"}])
        registry = ToolRegistry()

        manager.register_tools(registry)

        # Should not crash, just no tools registered
        assert len([k for k in registry._tools.keys() if k.startswith("empty:")]) == 0


def test_register_tools_reuses_cached_discovery_between_registries():
    """Repeated registry rebuilds should not re-run MCP tools/list discovery."""
    mock_server = Mock()
    mock_server.name = "deepwiki"
    mock_server.list_tools.return_value = [
        {"name": "ask_question", "description": "Ask repository questions"},
        {"name": "read_wiki", "description": "Read wiki pages"},
    ]

    with patch("src.mcp.MCPServer", return_value=mock_server):
        manager = MCPManager([{"name": "deepwiki", "url": "http://localhost:3000"}])
        first_registry = ToolRegistry()
        second_registry = ToolRegistry()

        manager.register_tools(first_registry)
        manager.register_tools(second_registry)

        assert "deepwiki:ask_question" in first_registry._tools
        assert "deepwiki:read_wiki" in second_registry._tools
        assert mock_server.list_tools.call_count == 1
