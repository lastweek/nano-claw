"""Tests for the local HTTP serve command output."""

from __future__ import annotations

import signal
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from src.cli.serve import (
    SERVE_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS,
    SERVE_TIMEOUT_KEEP_ALIVE_SECONDS,
    _GracefulUvicornServer,
    run_serve_command,
)
from src.database.connection import DEFAULT_HTTP_DATABASE_PATH


def test_run_serve_command_prints_access_urls_and_paths(
    monkeypatch,
    capsys,
    temp_dir,
    http_runtime_config,
):
    """Serve mode should print the chat/admin/health URLs and local backing paths."""
    fake_app = object()
    config_calls = []
    server_runs = []
    home_dir = temp_dir / "home"
    home_dir.mkdir()

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(temp_dir)
    monkeypatch.setattr(http_runtime_config.server, "db_path", DEFAULT_HTTP_DATABASE_PATH)
    monkeypatch.setitem(
        sys.modules,
        "src.server.app",
        SimpleNamespace(create_app=lambda runtime_config, repo_root: fake_app),
    )

    class FakeConfig:
        def __init__(self, app, host, port, timeout_keep_alive, timeout_graceful_shutdown):
            config_calls.append((app, host, port, timeout_keep_alive, timeout_graceful_shutdown))

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self.handle_exit = lambda sig, frame: None

        def run(self):
            server_runs.append(self.config)

    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(Config=FakeConfig, Server=FakeServer),
    )

    run_serve_command([], http_runtime_config)
    output = capsys.readouterr().out

    assert "HTTP server: http://127.0.0.1:8765" in output
    assert "Chat UI: http://127.0.0.1:8765/" in output
    assert "Admin UI: http://127.0.0.1:8765/admin" in output
    assert "Health: http://127.0.0.1:8765/api/v1/health" in output
    assert "Repo root:" in output
    assert temp_dir.name in output
    assert f"DB path: {(home_dir / '.babyclaw' / 'state.db').resolve()}" in output
    assert config_calls == [
        (
            fake_app,
            "127.0.0.1",
            8765,
            SERVE_TIMEOUT_KEEP_ALIVE_SECONDS,
            SERVE_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS,
        )
    ]
    assert len(server_runs) == 1


def test_run_serve_command_warns_for_non_loopback_host(
    monkeypatch,
    capsys,
    temp_dir,
    http_runtime_config,
):
    """Non-loopback serve hosts should print the no-auth warning."""
    monkeypatch.chdir(temp_dir)
    monkeypatch.setitem(
        sys.modules,
        "src.server.app",
        SimpleNamespace(create_app=lambda runtime_config, repo_root: object()),
    )

    class FakeConfig:
        def __init__(self, *args, **kwargs):
            return

    class FakeServer:
        def __init__(self, config):
            self.handle_exit = lambda sig, frame: None

        def run(self):
            return

    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(Config=FakeConfig, Server=FakeServer),
    )

    run_serve_command(["--host", "0.0.0.0"], http_runtime_config)
    output = capsys.readouterr().out

    assert "Warning: admin/chat HTTP mode has no auth; restrict access to a trusted network." in output


def test_graceful_uvicorn_server_marks_shutdown_and_swallows_keyboard_interrupt():
    """SIGINT handling should mark app shutdown, close SSE subscribers, and suppress traceback exit."""
    event_bus = Mock()
    app = SimpleNamespace(state=SimpleNamespace(shutdown_requested=False, event_bus=event_bus))

    class FakeServer:
        def __init__(self):
            self.handle_exit = Mock()

        def run(self):
            self.handle_exit(signal.SIGINT, None)
            raise KeyboardInterrupt()

    fake_server = FakeServer()
    server = _GracefulUvicornServer(fake_server, app)

    server.run()

    assert app.state.shutdown_requested is True
    event_bus.close_all.assert_called_once()
    fake_server.handle_exit.assert_called_once_with(signal.SIGINT, None)
