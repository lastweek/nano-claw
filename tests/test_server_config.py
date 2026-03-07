"""Tests for the local HTTP server config surface."""

from src.config import Config
from src.database.connection import DEFAULT_HTTP_DATABASE_PATH


def test_server_defaults(monkeypatch):
    """Server config should expose the documented local defaults."""
    monkeypatch.delenv("SERVER_HOST", raising=False)
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.delenv("SERVER_DB_PATH", raising=False)
    monkeypatch.delenv("SERVER_MAX_PARALLEL_RUNS", raising=False)
    monkeypatch.delenv("SERVER_SERVE_UI", raising=False)
    monkeypatch.delenv("SERVER_SSE_HEARTBEAT_SECONDS", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLED", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_FINDER", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_CALENDAR", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_NOTES", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_REMINDERS", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_MESSAGES", raising=False)

    runtime_config = Config()

    assert runtime_config.server.host == "127.0.0.1"
    assert runtime_config.server.port == 8765
    assert runtime_config.server.db_path == DEFAULT_HTTP_DATABASE_PATH
    assert runtime_config.server.max_parallel_runs == 1
    assert runtime_config.server.serve_ui is True
    assert runtime_config.server.sse_heartbeat_seconds == 10
    assert runtime_config.memory.enabled is True
    assert runtime_config.memory.root_dir == "~/.nano-claw/sessions"
    assert runtime_config.memory.auto_load_memory is True
    assert runtime_config.memory.max_auto_chars == 4000
    assert runtime_config.memory.max_search_results == 10
    assert runtime_config.macos_tools.enabled is True
    assert runtime_config.macos_tools.timeout_seconds == 10
    assert runtime_config.macos_tools.enable_finder is True
    assert runtime_config.macos_tools.enable_calendar is True
    assert runtime_config.macos_tools.enable_notes is True
    assert runtime_config.macos_tools.enable_reminders is True
    assert runtime_config.macos_tools.enable_messages is True


def test_server_env_overrides(monkeypatch):
    """SERVER_* env vars should override server defaults."""
    monkeypatch.setenv("SERVER_HOST", "127.0.0.2")
    monkeypatch.setenv("SERVER_PORT", "9000")
    monkeypatch.setenv("SERVER_DB_PATH", "tmp/http.db")
    monkeypatch.setenv("SERVER_MAX_PARALLEL_RUNS", "3")
    monkeypatch.setenv("SERVER_SERVE_UI", "false")
    monkeypatch.setenv("SERVER_SSE_HEARTBEAT_SECONDS", "4")
    monkeypatch.setenv("MEMORY_ENABLED", "true")
    monkeypatch.setenv("MEMORY_ROOT_DIR", "tmp/memory")
    monkeypatch.setenv("MEMORY_AUTO_LOAD_MEMORY", "false")
    monkeypatch.setenv("MEMORY_MAX_AUTO_CHARS", "1024")
    monkeypatch.setenv("MEMORY_MAX_SEARCH_RESULTS", "5")
    monkeypatch.setenv("MACOS_TOOLS_ENABLED", "true")
    monkeypatch.setenv("MACOS_TOOLS_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("MACOS_TOOLS_ENABLE_CALENDAR", "false")
    monkeypatch.setenv("MACOS_TOOLS_ENABLE_MESSAGES", "false")

    runtime_config = Config()

    assert runtime_config.server.host == "127.0.0.2"
    assert runtime_config.server.port == 9000
    assert runtime_config.server.db_path == "tmp/http.db"
    assert runtime_config.server.max_parallel_runs == 3
    assert runtime_config.server.serve_ui is False
    assert runtime_config.server.sse_heartbeat_seconds == 4
    assert runtime_config.memory.enabled is True
    assert runtime_config.memory.root_dir == "tmp/memory"
    assert runtime_config.memory.auto_load_memory is False
    assert runtime_config.memory.max_auto_chars == 1024
    assert runtime_config.memory.max_search_results == 5
    assert runtime_config.macos_tools.enabled is True
    assert runtime_config.macos_tools.timeout_seconds == 15
    assert runtime_config.macos_tools.enable_calendar is False
    assert runtime_config.macos_tools.enable_messages is False
