"""Tests for the local HTTP server config surface."""

from src.config import Config


def test_server_defaults(monkeypatch):
    """Server config should expose the documented local defaults."""
    monkeypatch.delenv("SERVER_HOST", raising=False)
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.delenv("SERVER_DB_PATH", raising=False)
    monkeypatch.delenv("SERVER_MAX_PARALLEL_RUNS", raising=False)
    monkeypatch.delenv("SERVER_SERVE_UI", raising=False)
    monkeypatch.delenv("SERVER_SSE_HEARTBEAT_SECONDS", raising=False)

    runtime_config = Config()

    assert runtime_config.server.host == "127.0.0.1"
    assert runtime_config.server.port == 8765
    assert runtime_config.server.db_path == ".nano-claw/state.db"
    assert runtime_config.server.max_parallel_runs == 1
    assert runtime_config.server.serve_ui is True
    assert runtime_config.server.sse_heartbeat_seconds == 10


def test_server_env_overrides(monkeypatch):
    """SERVER_* env vars should override server defaults."""
    monkeypatch.setenv("SERVER_HOST", "127.0.0.2")
    monkeypatch.setenv("SERVER_PORT", "9000")
    monkeypatch.setenv("SERVER_DB_PATH", "tmp/http.db")
    monkeypatch.setenv("SERVER_MAX_PARALLEL_RUNS", "3")
    monkeypatch.setenv("SERVER_SERVE_UI", "false")
    monkeypatch.setenv("SERVER_SSE_HEARTBEAT_SECONDS", "4")

    runtime_config = Config()

    assert runtime_config.server.host == "127.0.0.2"
    assert runtime_config.server.port == 9000
    assert runtime_config.server.db_path == "tmp/http.db"
    assert runtime_config.server.max_parallel_runs == 3
    assert runtime_config.server.serve_ui is False
    assert runtime_config.server.sse_heartbeat_seconds == 4
