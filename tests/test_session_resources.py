"""Tests for session resource construction and registry behavior."""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.context import CompactedContextSummary, Context
from src.server.event_bus import TurnEventBus
from src.server.session_registry import SessionRegistry
from src.server.session_resources import SessionResources, build_session_resources
from src.server.session_runtime import SessionRuntime
from src.database.session_database import SessionDatabase


class _FakeLogger:
    def __init__(self) -> None:
        self.closed_statuses: list[str] = []

    def close(self, status: str = "completed") -> None:
        self.closed_statuses.append(status)


def test_session_resources_close_attempts_all_cleanup() -> None:
    """Resource cleanup should still attempt MCP shutdown if logger close fails."""

    class FailingLogger:
        def close(self, status: str = "completed") -> None:
            raise RuntimeError(f"logger close failed: {status}")

    class FakeMCPManager:
        def __init__(self) -> None:
            self.closed_count = 0

        def close_all(self) -> None:
            self.closed_count += 1

    fake_mcp = FakeMCPManager()
    resources = SessionResources(
        agent=SimpleNamespace(),
        context=Context(cwd=Path.cwd()),
        logger=FailingLogger(),
        mcp_manager=fake_mcp,
    )

    with pytest.raises(RuntimeError, match="logger close failed"):
        resources.close(status="error")

    assert fake_mcp.closed_count == 1


def test_build_session_resources_hydrates_context_from_snapshot(
    monkeypatch,
    temp_dir,
    http_runtime_config,
):
    """Session resources should hydrate transcript and summary from the database snapshot."""

    class FakeAgent:
        def __init__(self, _llm, _tool_registry, context, **_kwargs) -> None:
            self.context = context
            self.request_metrics = []

        def run_stream(self, _user_message: str):
            return iter(())

    monkeypatch.setattr(
        "src.server.session_resources.SkillManager",
        lambda repo_root: SimpleNamespace(discover=lambda: None),
    )
    monkeypatch.setattr("src.server.session_resources.SubagentManager", lambda runtime_config=None: object())
    monkeypatch.setattr("src.server.session_resources.build_tool_registry", lambda **_kwargs: object())
    monkeypatch.setattr("src.server.session_resources.LLMClient", lambda runtime_config=None: object())
    monkeypatch.setattr("src.server.session_resources.SessionLogger", lambda *_args, **_kwargs: _FakeLogger())
    monkeypatch.setattr("src.server.session_resources.Agent", FakeAgent)

    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("Hydrate")
    turn = database.create_turn(session.id, "hello")

    context = Context(cwd=temp_dir, session_id=session.id)
    context.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Echo: hello"},
    ]
    context.set_summary(
        CompactedContextSummary(
            updated_at="2026-03-04T00:00:00",
            compaction_count=1,
            covered_turn_count=1,
            covered_message_count=2,
            rendered_text="summary",
            payload={"origin": "test"},
        )
    )
    database.replace_session_snapshot(session.id, turn.id, context)

    resources = build_session_resources(session.id, http_runtime_config, temp_dir, database)

    assert [message["content"] for message in resources.context.get_messages()] == [
        "hello",
        "Echo: hello",
    ]
    assert resources.context.get_summary() is not None
    assert resources.context.get_summary().rendered_text == "summary"
    resources.close()


def test_build_session_resources_failure_closes_mcp_and_logger(
    monkeypatch,
    temp_dir,
    http_runtime_config,
):
    """Resource build failures should release partially-created logger and MCP manager."""

    class FakeMCPManager:
        def __init__(self) -> None:
            self.closed_count = 0

        def close_all(self) -> None:
            self.closed_count += 1

    fake_mcp = FakeMCPManager()
    created_loggers: list[_FakeLogger] = []

    def fake_logger_factory(*_args, **_kwargs):
        logger = _FakeLogger()
        created_loggers.append(logger)
        return logger

    def failing_agent(*_args, **_kwargs):
        raise RuntimeError("synthetic runtime build failure")

    monkeypatch.setattr(
        "src.server.session_resources.SkillManager",
        lambda repo_root: SimpleNamespace(discover=lambda: None),
    )
    monkeypatch.setattr("src.server.session_resources._build_mcp_manager", lambda _cfg: fake_mcp)
    monkeypatch.setattr("src.server.session_resources.SubagentManager", lambda runtime_config=None: object())
    monkeypatch.setattr("src.server.session_resources.build_tool_registry", lambda **_kwargs: object())
    monkeypatch.setattr("src.server.session_resources.LLMClient", lambda runtime_config=None: object())
    monkeypatch.setattr("src.server.session_resources.SessionLogger", fake_logger_factory)
    monkeypatch.setattr("src.server.session_resources.Agent", failing_agent)

    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("HTTP")

    with pytest.raises(RuntimeError, match="synthetic runtime build failure"):
        build_session_resources(
            session.id,
            http_runtime_config,
            temp_dir,
            database,
        )

    assert fake_mcp.closed_count == 1
    assert len(created_loggers) == 1
    assert created_loggers[0].closed_statuses == ["error"]


def test_session_registry_ensure_runtime_is_lazy_and_idempotent(temp_dir, http_runtime_config):
    """Registry should construct one runtime lazily and reuse it for the same session."""

    class FakeRuntimeLogger:
        def __init__(self) -> None:
            self.closed_statuses: list[str] = []

        def close(self, status: str = "completed") -> None:
            self.closed_statuses.append(status)

    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("Registry")
    build_calls: list[str] = []

    def fake_resources_factory(session_id, runtime_config, repo_root, session_database):
        build_calls.append(session_id)
        assert runtime_config is http_runtime_config
        assert session_database is database
        return SessionResources(
            agent=SimpleNamespace(run_stream=lambda _input: iter(()), request_metrics=[]),
            context=Context(cwd=repo_root, session_id=session_id),
            logger=FakeRuntimeLogger(),
            mcp_manager=None,
        )

    registry = SessionRegistry(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
        database=database,
        event_bus=TurnEventBus(),
        resources_factory=fake_resources_factory,
    )

    assert registry.get_runtime(session.id) is None
    runtime_first = registry.ensure_runtime(session.id)
    runtime_second = registry.ensure_runtime(session.id)

    assert runtime_first is runtime_second
    assert build_calls == [session.id]
    registry.close_all()


def test_session_registry_ensure_runtime_coalesces_concurrent_builds(temp_dir, http_runtime_config):
    """Concurrent first access for one session should build exactly one runtime."""

    class FakeRuntimeLogger:
        def close(self, status: str = "completed") -> None:
            return

    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("ConcurrentRegistry")
    build_calls: list[str] = []

    def fake_resources_factory(session_id, runtime_config, repo_root, session_database):
        build_calls.append(session_id)
        time.sleep(0.05)
        return SessionResources(
            agent=SimpleNamespace(run_stream=lambda _input: iter(()), request_metrics=[]),
            context=Context(cwd=repo_root, session_id=session_id),
            logger=FakeRuntimeLogger(),
            mcp_manager=None,
        )

    registry = SessionRegistry(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
        database=database,
        event_bus=TurnEventBus(),
        resources_factory=fake_resources_factory,
    )

    results: list[object] = []

    def load_runtime() -> None:
        results.append(registry.ensure_runtime(session.id))

    first = threading.Thread(target=load_runtime)
    second = threading.Thread(target=load_runtime)
    first.start()
    second.start()
    first.join()
    second.join()

    assert len(results) == 2
    assert results[0] is results[1]
    assert build_calls == [session.id]
    registry.close_all()


def test_session_runtime_close_times_out_when_worker_is_stuck(
    monkeypatch,
    temp_dir,
    http_runtime_config,
):
    """Closing a stuck runtime should return after the configured join timeout."""

    class FakeRuntimeLogger:
        def close(self, status: str = "completed") -> None:
            return

        def get_session_snapshot(self):
            return SimpleNamespace(
                session_dir="",
                llm_log="",
                events_log="",
                llm_call_count=0,
                tool_call_count=0,
                tools_used=[],
            )

    stop_event = threading.Event()

    class BlockingAgent:
        request_metrics: list[object] = []

        def run_stream(self, _input: str):
            stop_event.wait(timeout=1.0)
            return iter(())

    monkeypatch.setattr("src.server.session_runtime.SESSION_RUNTIME_CLOSE_TIMEOUT_SECONDS", 0.01)

    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("BlockingRuntime")

    runtime = SessionRuntime(
        session_id=session.id,
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
        database=database,
        event_bus=TurnEventBus(),
        resources_factory=lambda session_id, _config, repo_root, _store: SessionResources(
            agent=BlockingAgent(),
            context=Context(cwd=repo_root, session_id=session_id),
            logger=FakeRuntimeLogger(),
            mcp_manager=None,
        ),
    )

    runtime.submit_turn("blocked")
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if runtime.is_busy():
            break
        time.sleep(0.01)

    started_at = time.monotonic()
    runtime.close()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.2
    assert runtime._worker.is_alive()

    stop_event.set()
    runtime._worker.join(timeout=1.0)
    assert not runtime._worker.is_alive()
