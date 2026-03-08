"""Tests for SessionLogger."""

import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

from src.config import Config
from src.logger import SessionLogger


def _top_level_session_dirs(root: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in Path(root).iterdir()
            if not path.is_symlink() and path.is_dir() and (path / "session.json").exists()
        ],
        key=lambda path: path.name,
    )


def _top_level_session_dir(root: Path) -> Path:
    session_dirs = _top_level_session_dirs(root)
    assert len(session_dirs) == 1
    return session_dirs[0]


class TestSessionLogger:
    """Test SessionLogger functionality."""

    def test_default_logger_uses_test_runtime_root(self, temp_dir, monkeypatch):
        """Default SessionLogger should resolve under the pytest runtime root."""
        test_root = (temp_dir / "runtime").resolve()
        monkeypatch.setenv("NANO_CODER_TEST", "true")
        monkeypatch.setenv("NANO_CLAW_TEST_ROOT", str(test_root))
        monkeypatch.delenv("LOG_DIR", raising=False)
        runtime_config = Config()

        logger = SessionLogger(str(uuid.uuid4()), runtime_config=runtime_config, enabled=True)
        logger.start_session(
            cwd=str(temp_dir),
            provider="openai",
            model="gpt-4.1",
            base_url="https://example.invalid/v1",
            streaming_enabled=True,
        )
        turn_id = logger.start_turn(raw_user_input="hello", normalized_user_input="hello")
        logger.finish_turn(turn_id, "world", [], status="completed")
        logger.close()

        session_dir = logger.ensure_session_dir()
        assert session_dir.is_relative_to(test_root / "sessions")
        assert not session_dir.is_relative_to((Path.home() / ".nano-claw").resolve())

    def _build_logger(self, temp_dir, **kwargs):
        logger = SessionLogger(str(uuid.uuid4()), log_dir=str(temp_dir), enabled=True, **kwargs)
        logger.start_session(
            cwd=str(temp_dir),
            provider="openai",
            model="gpt-4.1",
            base_url="https://example.invalid/v1",
            streaming_enabled=True,
        )
        return logger

    def test_does_not_create_session_without_writes(self, temp_dir):
        """No session directory should be created if nothing is written."""
        logger = self._build_logger(temp_dir)
        logger.close()

        assert _top_level_session_dirs(temp_dir) == []
        assert not (Path(temp_dir) / "latest-session").exists()
        assert not (Path(temp_dir) / "latest.log").exists()

    def test_creates_session_directory_and_manifest(self, temp_dir):
        """A real write should create session.json, llm.log, and events.jsonl."""
        logger = self._build_logger(temp_dir)
        turn_id = logger.start_turn(raw_user_input="hello", normalized_user_input="hello")
        logger.finish_turn(turn_id, "world", [], status="completed")
        logger.close()

        session_dir = _top_level_session_dir(temp_dir)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-session-[a-f0-9-]+", session_dir.name)
        assert session_dir.name.endswith(f"-{logger.session_id}")
        assert (session_dir / "session.json").exists()
        assert (session_dir / "llm.log").exists()
        assert (session_dir / "events.jsonl").exists()
        assert (session_dir / "artifacts").exists()
        assert (Path(temp_dir) / "latest-session").exists()
        assert (Path(temp_dir) / "latest.log").exists()

        session = json.loads((session_dir / "session.json").read_text())
        assert session["status"] == "completed"
        assert session["turn_count"] == 1
        assert session["llm_log"] == "llm.log"
        assert session["events_log"] == "events.jsonl"
        assert session["artifacts_dir"] == "artifacts"

    def test_logs_llm_request_and_response_to_llm_log(self, temp_dir):
        """llm.log should contain full JSON request/response timeline blocks."""
        logger = self._build_logger(temp_dir)
        turn_id = logger.start_turn(raw_user_input="hello", normalized_user_input="hello")

        logger.log_llm_request(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=False,
            request_payload={
                "model": "gpt-4.1",
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "hello"},
                ],
                "tools": [{"type": "function", "function": {"name": "read_file"}}],
                "stream": False,
            },
        )
        logger.log_llm_response(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=False,
            response_payload={
                "id": "resp_1",
                "object": "chat.completion",
                "model": "gpt-4.1",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hi there", "tool_calls": None},
                    }
                ],
                "usage": None,
            },
            metrics={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15, "cached_tokens": 8},
        )
        logger.finish_turn(
            turn_id,
            "Hi there",
            [
                SimpleNamespace(
                    prompt_tokens=12,
                    completion_tokens=3,
                    total_tokens=15,
                    cached_tokens=8,
                )
            ],
        )
        logger.close()

        session_dir = _top_level_session_dir(temp_dir)
        llm_log = (session_dir / "llm.log").read_text()
        assert "STEP 0001 | SESSION START" in llm_log
        assert "STEP 0002 | TURN 0001 | TURN START" in llm_log
        assert "STEP 0003 | TURN 0001 | ITERATION 01 | LLM REQUEST | STREAM=false" in llm_log
        assert "REQUEST JSON" in llm_log
        assert "\"messages\"" in llm_log
        assert "\"tools\"" in llm_log
        assert "STEP 0004 | TURN 0001 | ITERATION 01 | LLM RESPONSE | STREAM=false" in llm_log
        assert "RESPONSE JSON" in llm_log
        assert "Hi there" in llm_log
        assert "\"prompt_tokens\": 12" in llm_log
        assert "STEP 0005 | TURN 0001 | TURN END" in llm_log
        assert "STEP 0006 | SESSION END" in llm_log

    def test_get_session_snapshot_returns_in_memory_aggregates(self, temp_dir):
        """The logger snapshot should expose current session aggregates without file parsing."""
        logger = self._build_logger(temp_dir)
        turn_id = logger.start_turn(raw_user_input="hello", normalized_user_input="hello")
        logger.log_llm_request(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=False,
            request_payload={
                "model": "gpt-4.1",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )
        logger.log_tool_call(
            turn_id=turn_id,
            iteration=0,
            tool_name="read_file",
            arguments={"file_path": "README.md"},
            tool_call_id="call_1",
        )
        logger.finish_turn(turn_id, "done", [], status="completed")

        snapshot = logger.get_session_snapshot()
        logger.close()

        assert snapshot.session_dir.endswith(logger.session_dir.name)
        assert snapshot.llm_log.endswith("llm.log")
        assert snapshot.events_log.endswith("events.jsonl")
        assert snapshot.llm_call_count == 1
        assert snapshot.tool_call_count == 1
        assert snapshot.tools_used == ["read_file"]

    def test_logs_structured_events_and_spills_large_payloads(self, temp_dir):
        """Tool and skill events should be inline in llm.log and structured in events."""
        logger = self._build_logger(temp_dir)
        turn_id = logger.start_turn(raw_user_input="hello", normalized_user_input="hello")

        logger.log_skill_event(turn_id, "preload", skill_name="pdf", reason="explicit")
        logger.log_tool_call(
            turn_id=turn_id,
            iteration=0,
            tool_name="read_file",
            arguments={"file_path": "README.md"},
            tool_call_id="call_1",
        )
        logger.log_tool_result(
            turn_id=turn_id,
            iteration=0,
            tool_name="read_file",
            result={"output": "x" * 9000},
            tool_call_id="call_1",
        )
        logger.log_error(turn_id=turn_id, phase="agent.run", message="boom", details={"kind": "ValueError"})
        logger.finish_turn(turn_id, "done", [], status="error", error={"message": "boom"})
        logger.close(status="error")

        session_dir = _top_level_session_dir(temp_dir)
        events = [
            json.loads(line)
            for line in (session_dir / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        kinds = [event["kind"] for event in events]
        assert "session_started" in kinds
        assert "turn_started" in kinds
        assert "skill_event" in kinds
        assert "tool_call" in kinds
        assert "tool_result" in kinds
        assert "error" in kinds
        assert "turn_completed" in kinds
        assert "session_completed" in kinds

        tool_result = next(event for event in events if event["kind"] == "tool_result")
        assert tool_result["tool_name"] == "read_file"
        assert "payload_path" in tool_result
        assert "timeline_seq" in tool_result
        artifact_path = session_dir / tool_result["payload_path"]
        assert artifact_path.exists()
        assert artifact_path.read_text().startswith("{")

        llm_log = (session_dir / "llm.log").read_text()
        assert "SKILL EVENT" in llm_log
        assert "TOOL CALL" in llm_log
        assert "TOOL RESULT" in llm_log
        assert "\"file_path\": \"README.md\"" in llm_log
        assert "\"reason\": \"explicit\"" in llm_log

    def test_logs_context_compaction_lifecycle_and_request_kind(self, temp_dir):
        """Compaction lifecycle and request-kind labeling should be explicit in the logs."""
        logger = self._build_logger(temp_dir)
        turn_id = logger.start_turn(raw_user_input="hello", normalized_user_input="hello")

        logger.log_context_compaction_event(
            turn_id=turn_id,
            stage="started",
            reason="threshold_reached",
            covered_turn_count=4,
            retained_turn_count=2,
        )
        logger.log_llm_request(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=False,
            request_kind="context_compaction",
            request_payload={
                "model": "gpt-4.1",
                "messages": [{"role": "system", "content": "Summarize"}],
                "stream": False,
            },
        )
        logger.log_llm_response(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=False,
            request_kind="context_compaction",
            response_payload={
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
            },
            metrics={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13, "cached_tokens": 0},
        )
        logger.log_context_compaction_event(
            turn_id=turn_id,
            stage="completed",
            reason="threshold_reached",
            covered_turn_count=4,
            retained_turn_count=2,
            before_tokens=180000,
            after_tokens=110000,
        )
        logger.finish_turn(turn_id, "done", [], status="completed")
        logger.close()

        session_dir = _top_level_session_dir(temp_dir)
        llm_log = (session_dir / "llm.log").read_text()
        assert "CONTEXT COMPACTION START" in llm_log
        assert "CONTEXT COMPACTION REQUEST" in llm_log
        assert "Request Kind: context_compaction" in llm_log
        assert "CONTEXT COMPACTION RESPONSE" in llm_log
        assert "CONTEXT COMPACTION END" in llm_log

        events = [
            json.loads(line)
            for line in (session_dir / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        kinds = [event["kind"] for event in events]
        assert "context_compaction_started" in kinds
        assert "context_compaction_completed" in kinds

    def test_logs_plan_lifecycle_and_request_kind(self, temp_dir):
        """Plan lifecycle and plan request-kind labeling should be explicit in the logs."""
        logger = self._build_logger(temp_dir)
        turn_id = logger.start_turn(raw_user_input="/plan start add workflow", normalized_user_input="add workflow")

        logger.log_plan_event(
            turn_id=None,
            stage="started",
            plan_id="plan-1234",
            status="draft",
            file_path=".nano-claw/plans/test.md",
            task="add workflow",
        )
        logger.log_llm_request(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=False,
            request_kind="plan_turn",
            request_payload={
                "model": "gpt-4.1",
                "messages": [{"role": "system", "content": "Plan"}],
                "stream": False,
            },
        )
        logger.log_llm_response(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=False,
            request_kind="plan_turn",
            response_payload={
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": ""}}],
            },
            metrics={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13, "cached_tokens": 0},
        )
        logger.log_plan_event(
            turn_id=turn_id,
            stage="submitted",
            plan_id="plan-1234",
            status="ready_for_review",
            file_path=".nano-claw/plans/test.md",
            summary="Plan ready",
        )
        logger.close()

        session_dir = _top_level_session_dir(temp_dir)
        llm_log = (session_dir / "llm.log").read_text()
        assert "PLAN START" in llm_log
        assert "PLAN REQUEST" in llm_log
        assert "Request Kind: plan_turn" in llm_log
        assert "PLAN RESPONSE" in llm_log
        assert "PLAN SUBMITTED" in llm_log

        events = [
            json.loads(line)
            for line in (session_dir / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        kinds = [event["kind"] for event in events]
        assert "plan_started" in kinds
        assert "plan_submitted" in kinds

    def test_logs_context_compaction_skipped_block(self, temp_dir):
        """Skipped compaction attempts should be explicit in llm.log and events."""
        logger = self._build_logger(temp_dir)

        logger.log_context_compaction_event(
            turn_id=None,
            stage="skipped",
            reason="not_enough_complete_turns",
            reason_text="Not enough complete turns are available to compact while retaining 2 recent turn(s).",
            complete_turn_count=0,
            evictable_turn_count=0,
            min_recent_turns=2,
            force=True,
        )
        logger.close()

        session_dir = _top_level_session_dir(temp_dir)
        llm_log = (session_dir / "llm.log").read_text()
        assert "CONTEXT COMPACTION SKIPPED" in llm_log
        assert "\"reason\": \"not_enough_complete_turns\"" in llm_log

    def test_records_compaction_history_snapshot(self, temp_dir):
        """Successful compactions should append structured snapshots to compaction-history.jsonl."""
        logger = self._build_logger(temp_dir)

        logger.record_compaction_snapshot(
            timestamp="2026-03-08T12:00:00",
            compaction_count=2,
            reason="manual_command",
            covered_turn_count=4,
            covered_message_count=8,
            retained_turn_count=2,
            before_tokens=1800,
            after_tokens=900,
            used_fallback=False,
            rendered_text="Session handoff for earlier turns:\n- Keep going",
            payload={"goal": ["Keep going"]},
        )
        logger.close()

        session_dir = _top_level_session_dir(temp_dir)
        history_path = session_dir / "compaction-history.jsonl"
        entries = [
            json.loads(line)
            for line in history_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        assert len(entries) == 1
        assert entries[0]["compaction_count"] == 2
        assert entries[0]["reason"] == "manual_command"
        assert entries[0]["payload"]["goal"] == ["Keep going"]

    def test_child_session_does_not_update_latest_symlinks_and_records_subagent_events(self, temp_dir):
        """Child sessions should nest under the parent and keep latest-session pointing at the parent."""
        parent_logger = self._build_logger(temp_dir)
        parent_turn_id = parent_logger.start_turn(raw_user_input="delegate", normalized_user_input="delegate")
        parent_session_dir = parent_logger.ensure_session_dir()

        child_logger = SessionLogger(
            str(uuid.uuid4()),
            log_dir=str(parent_session_dir / "subagents"),
            enabled=True,
            update_latest_symlinks=False,
            session_kind="subagent",
            parent_session_id=parent_logger.session_id,
            parent_turn_id=parent_turn_id,
            subagent_id="sa_0001_abcd1234",
            subagent_label="research-logging",
        )
        child_logger.start_session(
            cwd=str(temp_dir),
            provider="openai",
            model="gpt-4.1",
            base_url="https://example.invalid/v1",
            streaming_enabled=False,
        )
        child_turn_id = child_logger.start_turn(raw_user_input="brief", normalized_user_input="brief")
        child_logger.finish_turn(child_turn_id, "child done", [], status="completed")
        child_logger.close()

        parent_logger.log_subagent_event(
            turn_id=parent_turn_id,
            stage="started",
            subagent_id="sa_0001_abcd1234",
            label="research-logging",
            task="inspect logging",
            session_dir=str(child_logger.session_dir),
            llm_log=str(child_logger.get_llm_log_path()),
            events_log=str(child_logger.get_events_path()),
        )
        parent_logger.log_subagent_event(
            turn_id=parent_turn_id,
            stage="completed",
            subagent_id="sa_0001_abcd1234",
            label="research-logging",
            session_dir=str(child_logger.session_dir),
            llm_log=str(child_logger.get_llm_log_path()),
            events_log=str(child_logger.get_events_path()),
            summary="summary",
            status="completed",
        )
        parent_logger.finish_turn(parent_turn_id, "done", [], status="completed")
        parent_logger.close()

        latest_session = (Path(temp_dir) / "latest-session").resolve()
        assert latest_session == parent_session_dir.resolve()
        child_session_dir = child_logger.session_dir
        assert child_session_dir is not None
        assert child_session_dir.parent.name == "subagents"

        child_session = json.loads((child_session_dir / "session.json").read_text())
        assert child_session["session_kind"] == "subagent"
        assert child_session["parent_session_id"] == parent_logger.session_id
        assert child_session["parent_turn_id"] == parent_turn_id
        assert child_session["subagent_id"] == "sa_0001_abcd1234"

        parent_llm_log = (parent_session_dir / "llm.log").read_text()
        assert "SUBAGENT START" in parent_llm_log
        assert "SUBAGENT END" in parent_llm_log
        assert "research-logging" in parent_llm_log

    def test_async_mode_preserves_valid_output(self, temp_dir):
        """Async logging should still produce readable files."""
        logger = self._build_logger(temp_dir, async_mode=True)
        turn_id = logger.start_turn(raw_user_input="hi", normalized_user_input="hi")
        logger.log_llm_request(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=True,
            request_payload={
                "model": "gpt-4.1",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        logger.log_llm_response(
            turn_id=turn_id,
            iteration=0,
            provider="openai",
            model="gpt-4.1",
            stream=True,
            response_payload={
                "object": "chat.completion.stream.reconstructed",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hello"}}],
            },
            metrics={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cached_tokens": 0},
        )
        logger.finish_turn(turn_id, "hello", [], status="completed")
        logger.close()

        session_dir = _top_level_session_dir(temp_dir)
        llm_log = (session_dir / "llm.log").read_text()
        assert "LLM REQUEST" in llm_log
        assert "LLM RESPONSE" in llm_log

        events = [
            json.loads(line)
            for line in (session_dir / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert events[0]["kind"] == "session_started"
        assert events[-1]["kind"] == "session_completed"

    def test_async_mode_uses_transport_and_flushes_on_close(self, temp_dir, monkeypatch):
        """Async mode should submit writes through the transport and close it cleanly."""
        submit_calls = []
        close_timeouts = []

        class TrackingTransport:
            def __init__(self, on_error):
                self.on_error = on_error

            def submit(self, func, *args, **kwargs):
                submit_calls.append(func.__name__)
                func(*args, **kwargs)

            def close(self, *, timeout):
                close_timeouts.append(timeout)

        monkeypatch.setattr("src.logger.AsyncWriteTransport", TrackingTransport)

        logger = self._build_logger(temp_dir, async_mode=True)
        turn_id = logger.start_turn(raw_user_input="hi", normalized_user_input="hi")
        logger.finish_turn(turn_id, "hello", [], status="completed")
        logger.close()

        assert submit_calls
        assert close_timeouts == [5.0]
