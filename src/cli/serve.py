"""CLI entrypoint for the local HTTP server."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import FrameType
from typing import Any

from src.config import Config
from src.database.connection import resolve_http_database_path

SERVE_TIMEOUT_KEEP_ALIVE_SECONDS = 1
SERVE_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS = 2


def _begin_http_shutdown(app: Any) -> None:
    if getattr(app.state, "shutdown_requested", False):
        return
    app.state.shutdown_requested = True
    event_bus = getattr(app.state, "event_bus", None)
    if event_bus is not None:
        event_bus.close_all()


class _GracefulUvicornServer:
    """Uvicorn server wrapper that notifies app state before connection drain."""

    def __init__(self, server: Any, app: Any) -> None:
        self._server = server
        self._app = app
        self._original_handle_exit = server.handle_exit

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        _begin_http_shutdown(self._app)
        self._original_handle_exit(sig, frame)

    def run(self) -> None:
        self._server.handle_exit = self.handle_exit
        try:
            self._server.run()
        except KeyboardInterrupt:
            # Uvicorn re-raises SIGINT after shutdown; treat it as a normal local stop.
            return
        finally:
            self._server.handle_exit = self._original_handle_exit


def build_serve_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the serve subcommand."""
    parser = argparse.ArgumentParser(prog="babyclaw serve")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def run_serve_command(args: list[str], runtime_config: Config) -> None:
    """Launch the local FastAPI app using the current repo configuration."""
    parser = build_serve_parser()
    parsed = parser.parse_args(args)
    try:
        from src.server.app import create_app
    except ModuleNotFoundError:
        parser.error(
            "HTTP mode requires FastAPI dependencies. "
            "Install with: python -m pip install -r requirements.txt"
        )

    try:
        import uvicorn
    except ModuleNotFoundError:
        parser.error(
            "HTTP mode requires uvicorn. "
            "Install with: python -m pip install -r requirements.txt"
        )

    host = parsed.host or runtime_config.server.host
    port = parsed.port or runtime_config.server.port
    repo_root = Path.cwd()
    db_path = resolve_http_database_path(runtime_config.server.db_path, repo_root)

    print(f"HTTP server: http://{host}:{port}")
    print(f"Chat UI: http://{host}:{port}/")
    print(f"Admin UI: http://{host}:{port}/admin")
    print(f"Health: http://{host}:{port}/api/v1/health")
    print(f"Repo root: {repo_root}")
    print(f"DB path: {db_path}")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("Warning: admin/chat HTTP mode has no auth; restrict access to a trusted network.")

    app = create_app(runtime_config=runtime_config, repo_root=repo_root)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        timeout_keep_alive=SERVE_TIMEOUT_KEEP_ALIVE_SECONDS,
        timeout_graceful_shutdown=SERVE_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = _GracefulUvicornServer(uvicorn.Server(config), app)
    server.run()
