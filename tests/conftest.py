"""Pytest fixtures and test utilities for nano-claw."""

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

os.environ["NANO_CODER_TEST"] = "true"
_EXISTING_TEST_ROOT = os.environ.get("NANO_CLAW_TEST_ROOT")
_CREATED_TEST_ROOT = _EXISTING_TEST_ROOT is None
_TEST_RUNTIME_ROOT = Path(_EXISTING_TEST_ROOT or tempfile.mkdtemp(prefix="nano-claw-test-root-")).resolve()
os.environ["NANO_CLAW_TEST_ROOT"] = str(_TEST_RUNTIME_ROOT)

from src.config import Config
from src.context import CompactedContextSummary, Context
from src.logger_types import SessionLogSnapshot
from src.server import app as server_app
from src.server import session_resources as http_session_resources
from src.tools import ToolRegistry
from src.tools.bash import BashTool
from src.tools.read import ReadTool
from src.tools.write import WriteTool
from src.database.session_database import deserialize_session_summary


def pytest_sessionfinish(session, exitstatus):
    """Remove the pytest-owned runtime root after the test session."""
    if _CREATED_TEST_ROOT:
        shutil.rmtree(_TEST_RUNTIME_ROOT, ignore_errors=True)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def test_context(temp_dir):
    """Create a test context with temp directory as cwd."""
    return Context(cwd=temp_dir)


@pytest.fixture
def tool_registry():
    """Create a tool registry with all standard tools."""
    registry = ToolRegistry()
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(BashTool())
    return registry


@pytest.fixture
def sample_file(temp_dir):
    """Create a sample test file."""
    file_path = temp_dir / "test.txt"
    file_path.write_text("Hello\nWorld\nTest")
    return file_path


@pytest.fixture
def http_runtime_config(temp_dir, monkeypatch):
    """Build a deterministic runtime config for HTTP tests."""
    monkeypatch.setenv("NANO_CODER_TEST", "true")
    for env_name in list(os.environ.keys()):
        if env_name.startswith(("LLM_", "SERVER_", "ENABLE_LOGGING", "ASYNC_LOGGING", "LOG_DIR")):
            monkeypatch.delenv(env_name, raising=False)
    runtime_config = Config(
        {
            "llm": {
                "provider": "ollama",
                "model": "fake-model",
                "base_url": "http://localhost:11434/v1",
            },
            "logging": {
                "enabled": True,
                "async_mode": False,
                "log_dir": str(temp_dir / "sessions"),
                "buffer_size": 1,
            },
            "server": {
                "host": "127.0.0.1",
                "port": 8765,
                "db_path": str(temp_dir / "state.db"),
                "max_parallel_runs": 1,
                "serve_ui": True,
                "sse_heartbeat_seconds": 1,
            },
            "memory": {
                "enabled": False,
                "root_dir": str(temp_dir / "sessions"),
                "auto_load_memory": True,
                "max_auto_chars": 4000,
                "max_search_results": 10,
            },
            "mcp": {"servers": []},
        }
    )
    runtime_config.logging.log_dir = str(temp_dir / "sessions")
    runtime_config.server.db_path = str(temp_dir / "state.db")
    runtime_config.memory.root_dir = str(temp_dir / "sessions")
    return runtime_config


class FakeLogger:
    """Minimal logger surface used by HTTP route tests."""

    def __init__(self, base_dir: Path, session_id: str):
        session_dir = base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        llm_log = session_dir / "llm.log"
        events_log = session_dir / "events.jsonl"
        llm_log.write_text("", encoding="utf-8")
        events_log.write_text("", encoding="utf-8")
        self.session_dir = session_dir
        self.llm_log = llm_log
        self.events_log = events_log
        self.closed_status = None

    def close(self, status: str = "completed") -> None:
        self.closed_status = status

    def get_session_snapshot(self) -> SessionLogSnapshot:
        return SessionLogSnapshot(
            session_dir=str(self.session_dir),
            llm_log=str(self.llm_log),
            events_log=str(self.events_log),
            llm_call_count=0,
            tool_call_count=0,
            tools_used=[],
        )


class FakeAgent:
    """Deterministic streaming agent for HTTP integration tests."""

    def __init__(self, context: Context):
        self.context = context
        self.request_metrics = []

    def run_stream(self, user_message: str):
        if user_message == "cause failure":
            raise RuntimeError("synthetic failure")

        response = f"Echo: {user_message}"
        chunks = ["Echo: ", user_message]
        for chunk in chunks:
            if user_message == "slow stream":
                time.sleep(0.05)
            yield chunk

        self.context.add_message("user", user_message)
        self.context.add_message("assistant", response)

        if user_message == "compact session":
            self.context.set_summary(
                CompactedContextSummary(
                    updated_at="2026-03-04T00:00:00",
                    compaction_count=1,
                    covered_turn_count=max(1, len(self.context.messages) // 2),
                    covered_message_count=max(2, len(self.context.messages)),
                    rendered_text="Compacted summary",
                    payload={"origin": "test"},
                )
            )
            self.context.messages = self.context.messages[-2:]


@pytest.fixture
def patch_http_runtime(monkeypatch, temp_dir):
    """Patch HTTP runtime creation with deterministic fake runtime."""

    def _fake_build_session_resources(session_id, runtime_config, repo_root, database, memory_store=None):
        session_snapshot = database.get_session_snapshot(session_id)
        if session_snapshot is None:
            raise KeyError(f"Unknown session: {session_id}")

        context = Context(cwd=repo_root, session_id=session_id)
        context.messages = [
            {"role": message.role, "content": message.content}
            for message in session_snapshot.messages
        ]
        context.summary = deserialize_session_summary(session_snapshot.summary_json)
        context.active_skills = []
        context.session_mode = "build"

        logger = FakeLogger(temp_dir / "sessions", session_id)
        agent = FakeAgent(context)
        return http_session_resources.SessionResources(
            agent=agent,
            context=context,
            logger=logger,
            mcp_manager=None,
            memory_store=memory_store,
        )

    monkeypatch.setattr(
        server_app,
        "build_session_resources",
        _fake_build_session_resources,
    )
