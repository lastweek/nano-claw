"""Session logging for nano-claw."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from src.config import config
from src.logger_format import (
    ARTIFACT_SPILL_THRESHOLD,
    SECTION_RULE,
    SESSION_HEADER_RULE,
    UUID_TRUNC_LENGTH,
    append_json_block,
    append_text_block,
    pretty_json,
    render_timeline_block,
    serialize_payload,
)
from src.logger_transport import AsyncWriteTransport
from src.logger_types import SessionLogSnapshot, TurnState, sanitize_fragment
from src.session_paths import build_session_dir_name
from src.tools import (
    REQUEST_KIND_AGENT_TURN,
    REQUEST_KIND_CONTEXT_COMPACTION,
    REQUEST_KIND_PLAN_TURN,
)
from src.utils import env_truthy, resolve_path


DEFAULT_LOG_DIR = "~/.nano-claw/sessions"
LEGACY_LOG_DIR = "logs"


def migrate_legacy_log_dir(log_dir: str | Path, repo_root: Path) -> str | None:
    """Move repo-local logs into the default global log root when safe."""
    target_log_dir = resolve_path(log_dir, repo_root)
    default_global_log_dir = resolve_path(DEFAULT_LOG_DIR, repo_root)
    if target_log_dir != default_global_log_dir:
        return None

    legacy_log_dir = resolve_path(LEGACY_LOG_DIR, repo_root)
    if not legacy_log_dir.exists() or not any(legacy_log_dir.iterdir()):
        return None

    if target_log_dir.exists() and any(target_log_dir.iterdir()):
        return (
            "Legacy repo-local logs were left untouched at "
            f"{legacy_log_dir} because global logs already exist at {target_log_dir}."
        )

    target_log_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_log_dir.exists() and not any(target_log_dir.iterdir()):
        target_log_dir.rmdir()
    shutil.move(str(legacy_log_dir), str(target_log_dir))
    return f"Migrated legacy repo-local logs from {legacy_log_dir} to {target_log_dir}."


class SessionLogger:
    """Write per-session logs to a session directory."""

    def __init__(
        self,
        session_id: str,
        log_dir: Optional[str] = None,
        enabled: Optional[bool] = None,
        buffer_size: Optional[int] = None,
        async_mode: Optional[bool] = None,
        update_latest_symlinks: bool = True,
        session_kind: str = "primary",
        parent_session_id: Optional[str] = None,
        parent_turn_id: Optional[int] = None,
        subagent_id: Optional[str] = None,
        subagent_label: Optional[str] = None,
        session_title: Optional[str] = None,
        session_created_at: Optional[str] = None,
        runtime_config=None,
    ):
        """Initialize the logger."""
        self.session_id = session_id
        self.runtime_config = runtime_config or config

        if log_dir is None:
            log_dir = self.runtime_config.logging.log_dir
        if enabled is None:
            # Check environment variable for explicit true/false
            env_value = os.environ.get("ENABLE_LOGGING", "").lower()
            if env_value == "false":
                enabled = False
            elif env_truthy("ENABLE_LOGGING"):
                enabled = True
            else:
                enabled = self.runtime_config.logging.enabled
        if buffer_size is None:
            buffer_size = self.runtime_config.logging.buffer_size
        if async_mode is None:
            async_mode = self.runtime_config.logging.async_mode

        self.log_dir = resolve_path(log_dir)
        self.enabled = enabled
        self.async_mode = async_mode
        self.buffer_size = buffer_size
        self.update_latest_symlinks = update_latest_symlinks
        self.session_kind = session_kind
        self.parent_session_id = parent_session_id
        self.parent_turn_id = parent_turn_id
        self.subagent_id = subagent_id
        self.subagent_label = subagent_label
        self.session_title = session_title
        self.session_created_at = session_created_at

        self._lock = Lock()
        self._timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if self.session_kind == "subagent":
            label_fragment = sanitize_fragment(self.subagent_label or "subagent")
            id_fragment = sanitize_fragment((self.subagent_id or self.session_id)[:UUID_TRUNC_LENGTH])
            self._session_dir_name = f"subagent-{label_fragment}-{id_fragment}"
        else:
            self._session_dir_name = build_session_dir_name(
                self.session_id,
                title=self.session_title,
                created_at=self.session_created_at,
            )
        self.session_dir: Optional[Path] = None
        self._artifacts_dir: Optional[Path] = None
        self._session_json_path: Optional[Path] = None
        self._llm_log_path: Optional[Path] = None
        self._events_path: Optional[Path] = None
        self._initialized = False
        self._closed = False

        self._session_started_at = datetime.now().isoformat()
        self._session_ended_at: Optional[str] = None
        self._session_status = "open"
        self._session_metadata: Dict[str, Any] = {
            "cwd": None,
            "provider": None,
            "model": None,
            "base_url": None,
            "streaming_enabled": None,
        }
        self._turn_counter = 0
        self._event_seq = 0
        self._timeline_seq = 0
        self._llm_call_count = 0
        self._tool_call_count = 0
        self._session_tools_used: List[str] = []
        self._skill_event_count = 0
        self._error_count = 0
        self._turns: Dict[int, TurnState] = {}
        self._session_footer_written = False

        self._transport: AsyncWriteTransport | None = None
        if self.enabled and self.async_mode:
            self._transport = AsyncWriteTransport(self._write_fallback_error)

    def start_session(
        self,
        *,
        cwd: str,
        provider: str,
        model: str,
        base_url: Optional[str],
        streaming_enabled: bool,
    ) -> None:
        """Store session metadata for later manifest creation."""
        if not self.enabled:
            return

        self._session_metadata.update(
            {
                "cwd": str(cwd) if cwd is not None else None,
                "provider": str(provider) if provider is not None else None,
                "model": str(model) if model is not None else None,
                "base_url": str(base_url) if base_url is not None else None,
                "streaming_enabled": streaming_enabled,
            }
        )

    def start_turn(self, *, raw_user_input: str, normalized_user_input: str) -> int:
        """Start a new turn and return its id."""
        if not self.enabled:
            self._turn_counter += 1
            return self._turn_counter

        self._turn_counter += 1
        turn_id = self._turn_counter
        turn_state = TurnState(
            turn_id=turn_id,
            started_at=datetime.now().isoformat(),
            raw_user_input=raw_user_input,
            normalized_user_input=normalized_user_input,
        )
        self._turns[turn_id] = turn_state

        self._submit_write(self._record_turn_started, turn_state)
        self._submit_write(self._write_session_manifest)
        return turn_id

    def log_llm_request(
        self,
        turn_id: int,
        iteration: int,
        request_payload: Dict[str, Any],
        provider: str,
        model: str,
        stream: bool,
        request_kind: str = REQUEST_KIND_AGENT_TURN,
    ) -> None:
        """Log an LLM request block to llm.log."""
        if not self.enabled:
            return

        turn_state = self._turns.get(turn_id)
        if turn_state is not None:
            turn_state.llm_call_count += 1
        self._llm_call_count += 1

        self._submit_write(
            self._record_llm_request,
            turn_id,
            iteration,
            request_payload,
            provider,
            model,
            stream,
            request_kind,
        )
        self._submit_write(self._write_session_manifest)

    def log_llm_response(
        self,
        turn_id: int,
        iteration: int,
        response_payload: Dict[str, Any],
        provider: str,
        model: str,
        stream: bool,
        metrics: Optional[Dict[str, Any]] = None,
        request_kind: str = REQUEST_KIND_AGENT_TURN,
    ) -> None:
        """Log an LLM response block to llm.log."""
        if not self.enabled:
            return

        self._submit_write(
            self._record_llm_response,
            turn_id,
            iteration,
            response_payload,
            provider,
            model,
            stream,
            metrics or {},
            request_kind,
        )

    def log_context_compaction_event(
        self,
        *,
        turn_id: Optional[int],
        stage: str,
        **details: Any,
    ) -> None:
        """Log context compaction lifecycle to events.jsonl and llm.log."""
        if not self.enabled:
            return

        self._submit_write(self._record_context_compaction_event, turn_id, stage, details)

    def log_subagent_event(
        self,
        *,
        turn_id: Optional[int],
        stage: str,
        subagent_id: str,
        label: str,
        **details: Any,
    ) -> None:
        """Log a subagent lifecycle event to both parent outputs."""
        if not self.enabled:
            return

        self._submit_write(
            self._record_subagent_event,
            turn_id,
            stage,
            subagent_id,
            label,
            details,
        )

    def log_plan_event(
        self,
        *,
        turn_id: Optional[int],
        stage: str,
        **details: Any,
    ) -> None:
        """Log one plan lifecycle event to both session outputs."""
        if not self.enabled:
            return

        self._submit_write(self._record_plan_event, turn_id, stage, details)

    def log_skill_event(self, turn_id: int, event: str, **details: Any) -> None:
        """Log a skill event to events.jsonl and llm.log."""
        if not self.enabled:
            return

        self._skill_event_count += 1
        turn_state = self._turns.get(turn_id)
        skill_name = details.get("skill_name")
        if turn_state is not None and skill_name and skill_name not in turn_state.skills_used:
            turn_state.skills_used.append(skill_name)

        self._submit_write(self._record_skill_event, turn_id, event, details)
        self._submit_write(self._write_session_manifest)

    def log_tool_call(
        self,
        turn_id: int,
        iteration: int,
        tool_name: str,
        arguments: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> None:
        """Log a tool call to both outputs."""
        if not self.enabled:
            return

        self._tool_call_count += 1
        turn_state = self._turns.get(turn_id)
        if turn_state is not None:
            turn_state.tool_call_count += 1
            if tool_name not in turn_state.tools_used:
                turn_state.tools_used.append(tool_name)
        if tool_name not in self._session_tools_used:
            self._session_tools_used.append(tool_name)

        self._submit_write(
            self._record_tool_call,
            turn_id,
            iteration,
            tool_name,
            tool_call_id,
            arguments,
        )
        self._submit_write(self._write_session_manifest)

    def log_tool_result(
        self,
        turn_id: int,
        iteration: int,
        tool_name: str,
        result: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> None:
        """Log a tool result to both outputs."""
        if not self.enabled:
            return

        self._submit_write(
            self._record_tool_result,
            turn_id,
            iteration,
            tool_name,
            tool_call_id,
            result,
        )

    def finish_turn(
        self,
        turn_id: int,
        final_response: str,
        request_metrics: List[Any],
        status: str = "completed",
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Finalize a turn and update the session manifest."""
        if not self.enabled:
            return

        turn_state = self._turns.get(turn_id)
        if turn_state is None or turn_state.status != "open":
            return

        turn_state.status = status
        self._submit_write(
            self._record_turn_completed,
            turn_state,
            final_response,
            self._summarize_metrics(request_metrics),
            error,
        )
        self._submit_write(self._write_session_manifest)

    def log_error(
        self,
        *,
        turn_id: Optional[int],
        phase: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a structured error event."""
        if not self.enabled:
            return

        self._error_count += 1
        self._submit_write(
            self._record_error,
            turn_id,
            phase,
            message,
            details or {},
        )
        self._submit_write(self._write_session_manifest)

    def close(self, status: str = "completed") -> None:
        """Close the logger and flush all pending writes."""
        if not self.enabled or self._closed:
            return

        self._closed = True
        self._session_ended_at = datetime.now().isoformat()
        self._session_status = status

        should_finalize = self._initialized or any(
            (
                self._turn_counter,
                self._llm_call_count,
                self._tool_call_count,
                self._skill_event_count,
                self._error_count,
            )
        )
        if should_finalize:
            self._submit_write(self._record_session_completed)
            self._submit_write(self._write_session_manifest)

        if self.async_mode and self._transport is not None:
            self._transport.close(timeout=5.0)
            self._transport = None

    def __enter__(self) -> "SessionLogger":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close(status="error" if exc_type is not None else "completed")

    def _submit_write(self, func: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        """Submit a write operation in-order."""
        if not self.enabled:
            return

        if self.async_mode and self._transport is not None:
            self._transport.submit(func, *args, **kwargs)
            return

        func(*args, **kwargs)

    def _ensure_initialized(self) -> None:
        """Create the session directory and base files on first write."""
        if self._initialized:
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir = self.log_dir / self._session_dir_name
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._artifacts_dir = self.session_dir / "artifacts"
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._session_json_path = self.session_dir / "session.json"
        self._llm_log_path = self.session_dir / "llm.log"
        self._events_path = self.session_dir / "events.jsonl"

        if self.update_latest_symlinks:
            self._update_symlink(self.log_dir / "latest-session", self.session_dir)
            self._update_symlink(self.log_dir / "latest.log", self._llm_log_path)

        self._initialized = True
        self._write_session_manifest()
        self._record_session_started()

    def ensure_session_dir(self) -> Path:
        """Ensure the session directory exists and return it."""
        self._ensure_initialized()
        assert self.session_dir is not None
        return self.session_dir

    def get_llm_log_path(self) -> Path:
        """Return the llm.log path, creating the session directory if needed."""
        self._ensure_initialized()
        assert self._llm_log_path is not None
        return self._llm_log_path

    def get_events_path(self) -> Path:
        """Return the events.jsonl path, creating the session directory if needed."""
        self._ensure_initialized()
        assert self._events_path is not None
        return self._events_path

    def get_session_snapshot(self) -> SessionLogSnapshot:
        """Return the current in-memory aggregate session state."""
        if not self.enabled:
            return SessionLogSnapshot(
                session_dir="",
                llm_log="",
                events_log="",
                llm_call_count=self._llm_call_count,
                tool_call_count=self._tool_call_count,
                tools_used=list(self._session_tools_used),
            )

        session_dir = self.ensure_session_dir()
        return SessionLogSnapshot(
            session_dir=str(session_dir),
            llm_log=str(self.get_llm_log_path()),
            events_log=str(self.get_events_path()),
            llm_call_count=self._llm_call_count,
            tool_call_count=self._tool_call_count,
            tools_used=list(self._session_tools_used),
        )

    def _update_symlink(self, link_path: Path, target: Path) -> None:
        """Create or refresh a symlink when supported."""
        try:
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            relative_target = target.relative_to(self.log_dir)
            link_path.symlink_to(relative_target)
        except OSError:
            pass

    def _build_session_parent_fields(self) -> Dict[str, Any]:
        """Build common parent/session fields used across logging."""
        return {
            "session_kind": self.session_kind,
            "parent_session_id": self.parent_session_id,
            "parent_turn_id": self.parent_turn_id,
            "subagent_id": self.subagent_id,
            "subagent_label": self.subagent_label,
        }

    def _build_session_parent_lines(self) -> List[str]:
        """Build formatted parent/session metadata lines for display."""
        return [
            f"Session ID: {self.session_id}",
            f"Session Kind: {self.session_kind}",
            f"Parent Session ID: {self.parent_session_id}",
            f"Parent Turn ID: {self.parent_turn_id}",
            f"Subagent ID: {self.subagent_id}",
            f"Subagent Label: {self.subagent_label}",
        ]

    def _write_session_manifest(self) -> None:
        """Write session.json."""
        self._ensure_initialized()
        assert self._session_json_path is not None

        payload = {
            "schema_version": 1,
            "timeline_format_version": 2,
            "primary_debug_log": "llm.log",
            "session_id": self.session_id,
            **self._build_session_parent_fields(),
            "started_at": self._session_started_at,
            "ended_at": self._session_ended_at,
            "status": self._session_status,
            "cwd": self._session_metadata["cwd"],
            "provider": self._session_metadata["provider"],
            "model": self._session_metadata["model"],
            "base_url": self._session_metadata["base_url"],
            "streaming_enabled": self._session_metadata["streaming_enabled"],
            "turn_count": len(self._turns),
            "llm_call_count": self._llm_call_count,
            "tool_call_count": self._tool_call_count,
            "skill_event_count": self._skill_event_count,
            "error_count": self._error_count,
            "llm_log": "llm.log",
            "events_log": "events.jsonl",
            "artifacts_dir": "artifacts",
        }

        with self._lock:
            self._session_json_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
                encoding="utf-8",
            )

    def _next_timeline_seq(self) -> int:
        """Return the next timeline step id."""
        self._timeline_seq += 1
        return self._timeline_seq

    def _append_llm_log(self, text: str) -> None:
        """Append text to llm.log."""
        self._ensure_initialized()
        assert self._llm_log_path is not None
        with self._lock:
            with open(self._llm_log_path, "a", encoding="utf-8") as handle:
                handle.write(text)

    def _append_event(self, kind: str, timeline_seq: Optional[int], **fields: Any) -> None:
        """Append a structured event to events.jsonl."""
        self._ensure_initialized()
        assert self._events_path is not None

        self._event_seq += 1
        entry = {
            "seq": self._event_seq,
            "timeline_seq": timeline_seq,
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "kind": kind,
            **fields,
        }

        with self._lock:
            with open(self._events_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def _append_json_block(self, label: str, payload: Any) -> List[str]:
        """Render a JSON block for llm.log."""
        return append_json_block(label, payload)

    def _append_text_block(self, label: str, text: str) -> List[str]:
        """Render a text block for llm.log."""
        return append_text_block(label, text)

    def _write_timeline_block(
        self,
        *,
        rule: str,
        header: str,
        timestamp: str,
        metadata_lines: Optional[List[str]] = None,
        sections: Optional[List[str]] = None,
    ) -> None:
        """Write a fully formatted timeline block to llm.log."""
        self._append_llm_log(
            render_timeline_block(
                rule=rule,
                header=header,
                timestamp=timestamp,
                metadata_lines=metadata_lines,
                sections=sections,
            )
        )

    def _record_session_started(self) -> None:
        """Write the session start block and event."""
        if self._session_footer_written:
            return

        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        self._append_event(
            "session_started",
            timeline_seq,
            **self._build_session_parent_fields(),
            cwd=self._session_metadata["cwd"],
            provider=self._session_metadata["provider"],
            model=self._session_metadata["model"],
            base_url=self._session_metadata["base_url"],
            streaming_enabled=self._session_metadata["streaming_enabled"],
        )
        self._write_timeline_block(
            rule=SESSION_HEADER_RULE,
            header=f"STEP {timeline_seq:04d} | SESSION START",
            timestamp=timestamp,
            metadata_lines=self._build_session_parent_lines() + [
                f"CWD: {self._session_metadata['cwd']}",
                f"Provider: {self._session_metadata['provider']}",
                f"Model: {self._session_metadata['model']}",
                f"Base URL: {self._session_metadata['base_url']}",
                f"Streaming: {str(bool(self._session_metadata['streaming_enabled'])).lower()}",
            ],
        )

    def _record_session_completed(self) -> None:
        """Write the session end block and event."""
        if self._session_footer_written:
            return

        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        self._append_event(
            "session_completed",
            timeline_seq,
            status=self._session_status,
            ended_at=self._session_ended_at,
        )
        self._write_timeline_block(
            rule=SESSION_HEADER_RULE,
            header=f"STEP {timeline_seq:04d} | SESSION END",
            timestamp=timestamp,
            metadata_lines=[
                f"Status: {self._session_status}",
                f"Turns: {len(self._turns)}",
                f"LLM Calls: {self._llm_call_count}",
                f"Tool Calls: {self._tool_call_count}",
                f"Errors: {self._error_count}",
            ],
        )
        self._session_footer_written = True

    def _record_turn_started(self, turn_state: TurnState) -> None:
        """Write turn start to both logs."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        self._append_event(
            "turn_started",
            timeline_seq,
            turn_id=turn_state.turn_id,
            raw_user_input=turn_state.raw_user_input,
            normalized_user_input=turn_state.normalized_user_input,
        )
        sections = []
        sections.extend(self._append_text_block("RAW USER INPUT", turn_state.raw_user_input))
        sections.extend(self._append_text_block("NORMALIZED USER INPUT", turn_state.normalized_user_input))
        self._write_timeline_block(
            rule=SECTION_RULE,
            header=f"STEP {timeline_seq:04d} | TURN {turn_state.turn_id:04d} | TURN START",
            timestamp=timestamp,
            sections=sections,
        )

    def _record_llm_request(
        self,
        turn_id: int,
        iteration: int,
        request_payload: Dict[str, Any],
        provider: str,
        model: str,
        stream: bool,
        request_kind: str,
    ) -> None:
        """Write an LLM request block."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        sections = self._append_json_block("REQUEST JSON", request_payload)
        if request_kind == REQUEST_KIND_CONTEXT_COMPACTION:
            header = (
                f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | "
                f"CONTEXT COMPACTION REQUEST | STREAM={str(stream).lower()}"
            )
        elif request_kind == REQUEST_KIND_PLAN_TURN:
            header = (
                f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | ITERATION {iteration + 1:02d} | "
                f"PLAN REQUEST | STREAM={str(stream).lower()}"
            )
        else:
            header = (
                f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | ITERATION {iteration + 1:02d} | "
                f"LLM REQUEST | STREAM={str(stream).lower()}"
            )
        self._write_timeline_block(
            rule=SESSION_HEADER_RULE,
            header=header,
            timestamp=timestamp,
            metadata_lines=[f"Provider: {provider}", f"Model: {model}", f"Request Kind: {request_kind}"],
            sections=sections,
        )

    def _record_llm_response(
        self,
        turn_id: int,
        iteration: int,
        response_payload: Dict[str, Any],
        provider: str,
        model: str,
        stream: bool,
        metrics: Dict[str, Any],
        request_kind: str,
    ) -> None:
        """Write an LLM response block."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        sections = self._append_json_block("RESPONSE JSON", response_payload)
        if metrics:
            sections.extend(self._append_json_block("METRICS", metrics))
        if request_kind == REQUEST_KIND_CONTEXT_COMPACTION:
            header = (
                f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | "
                f"CONTEXT COMPACTION RESPONSE | STREAM={str(stream).lower()}"
            )
        elif request_kind == REQUEST_KIND_PLAN_TURN:
            header = (
                f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | ITERATION {iteration + 1:02d} | "
                f"PLAN RESPONSE | STREAM={str(stream).lower()}"
            )
        else:
            header = (
                f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | ITERATION {iteration + 1:02d} | "
                f"LLM RESPONSE | STREAM={str(stream).lower()}"
            )
        self._write_timeline_block(
            rule=SESSION_HEADER_RULE,
            header=header,
            timestamp=timestamp,
            metadata_lines=[f"Provider: {provider}", f"Model: {model}", f"Request Kind: {request_kind}"],
            sections=sections,
        )

    def _record_context_compaction_event(
        self,
        turn_id: Optional[int],
        stage: str,
        details: Dict[str, Any],
    ) -> None:
        """Write a context compaction lifecycle block and structured event."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        event_kind = f"context_compaction_{stage}"
        self._append_event(
            event_kind,
            timeline_seq,
            turn_id=turn_id,
            **details,
        )

        stage_label = {
            "started": "CONTEXT COMPACTION START",
            "completed": "CONTEXT COMPACTION END",
            "failed": "CONTEXT COMPACTION FAILED",
            "skipped": "CONTEXT COMPACTION SKIPPED",
        }.get(stage, "CONTEXT COMPACTION")
        if turn_id is None:
            header = f"STEP {timeline_seq:04d} | {stage_label}"
        else:
            header = f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | {stage_label}"
        self._write_timeline_block(
            rule=SECTION_RULE,
            header=header,
            timestamp=timestamp,
            sections=self._append_json_block("DETAILS", details),
        )

    def _record_subagent_event(
        self,
        turn_id: Optional[int],
        stage: str,
        subagent_id: str,
        label: str,
        details: Dict[str, Any],
    ) -> None:
        """Write a subagent lifecycle block and structured event."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        event_kind = f"subagent_{stage}"
        self._append_event(
            event_kind,
            timeline_seq,
            turn_id=turn_id,
            subagent_id=subagent_id,
            label=label,
            **details,
        )

        stage_label = {
            "started": "SUBAGENT START",
            "completed": "SUBAGENT END",
            "failed": "SUBAGENT FAILED",
        }.get(stage, "SUBAGENT")
        if turn_id is None:
            header = f"STEP {timeline_seq:04d} | {stage_label}"
        else:
            header = f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | {stage_label}"
        self._write_timeline_block(
            rule=SECTION_RULE,
            header=header,
            timestamp=timestamp,
            metadata_lines=[
                f"Subagent ID: {subagent_id}",
                f"Label: {label}",
                f"Session Dir: {details.get('session_dir')}",
                f"LLM Log: {details.get('llm_log')}",
                f"Events Log: {details.get('events_log')}",
            ],
            sections=self._append_json_block("DETAILS", details),
        )

    def _record_plan_event(
        self,
        turn_id: Optional[int],
        stage: str,
        details: Dict[str, Any],
    ) -> None:
        """Write a plan lifecycle block and structured event."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        event_kind = f"plan_{stage}"
        self._append_event(
            event_kind,
            timeline_seq,
            turn_id=turn_id,
            **details,
        )

        stage_label = {
            "started": "PLAN START",
            "written": "PLAN WRITTEN",
            "submitted": "PLAN SUBMITTED",
            "approved": "PLAN APPROVED",
            "rejected": "PLAN REJECTED",
            "execution_started": "PLAN EXECUTION START",
            "cleared": "PLAN CLEARED",
        }.get(stage, "PLAN EVENT")
        if turn_id is None:
            header = f"STEP {timeline_seq:04d} | {stage_label}"
        else:
            header = f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | {stage_label}"
        self._write_timeline_block(
            rule=SECTION_RULE,
            header=header,
            timestamp=timestamp,
            sections=self._append_json_block("DETAILS", details),
        )

    def _record_skill_event(self, turn_id: int, event: str, details: Dict[str, Any]) -> None:
        """Write a skill event block and structured event."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        self._append_event(
            "skill_event",
            timeline_seq,
            turn_id=turn_id,
            event=event,
            **details,
        )
        self._write_timeline_block(
            rule=SECTION_RULE,
            header=f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | SKILL EVENT",
            timestamp=timestamp,
            metadata_lines=[f"Event: {event}"],
            sections=self._append_json_block("DETAILS", details),
        )

    def _record_tool_call(
        self,
        turn_id: int,
        iteration: int,
        tool_name: str,
        tool_call_id: Optional[str],
        arguments: Dict[str, Any],
    ) -> None:
        """Write a tool call block and structured event."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        payload_fields = self._prepare_payload_fields(
            payload=arguments,
            turn_id=turn_id,
            stem=f"turn-{turn_id:04d}-iteration-{iteration + 1:02d}-tool-call-{tool_name}-args",
        )
        self._append_event(
            "tool_call",
            timeline_seq,
            turn_id=turn_id,
            iteration=iteration,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            **payload_fields,
        )
        self._write_timeline_block(
            rule=SECTION_RULE,
            header=f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | ITERATION {iteration + 1:02d} | TOOL CALL",
            timestamp=timestamp,
            metadata_lines=[
                f"Tool: {tool_name}",
                f"Tool Call ID: {tool_call_id}",
            ],
            sections=self._append_json_block("ARGUMENTS JSON", arguments),
        )

    def _record_tool_result(
        self,
        turn_id: int,
        iteration: int,
        tool_name: str,
        tool_call_id: Optional[str],
        result: Dict[str, Any],
    ) -> None:
        """Write a tool result block and structured event."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        payload_fields = self._prepare_payload_fields(
            payload=result,
            turn_id=turn_id,
            stem=f"turn-{turn_id:04d}-iteration-{iteration + 1:02d}-tool-result-{tool_name}",
        )
        status = "error" if "error" in result else "success"
        self._append_event(
            "tool_result",
            timeline_seq,
            turn_id=turn_id,
            iteration=iteration,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status=status,
            **payload_fields,
        )
        self._write_timeline_block(
            rule=SECTION_RULE,
            header=f"STEP {timeline_seq:04d} | TURN {turn_id:04d} | ITERATION {iteration + 1:02d} | TOOL RESULT",
            timestamp=timestamp,
            metadata_lines=[
                f"Tool: {tool_name}",
                f"Tool Call ID: {tool_call_id}",
                f"Status: {status}",
            ],
            sections=self._append_json_block("RESULT JSON", result),
        )

    def _record_turn_completed(
        self,
        turn_state: TurnState,
        final_response: str,
        metrics_summary: Dict[str, Any],
        error: Optional[Dict[str, Any]],
    ) -> None:
        """Write turn completion to both logs."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        event_payload = {
            "turn_id": turn_state.turn_id,
            "status": turn_state.status,
            "raw_user_input": turn_state.raw_user_input,
            "normalized_user_input": turn_state.normalized_user_input,
            "final_response_chars": len(final_response),
            "llm_call_count": turn_state.llm_call_count,
            "tool_call_count": turn_state.tool_call_count,
            "tools_used": turn_state.tools_used,
            "skills_used": turn_state.skills_used,
            "metrics": metrics_summary,
        }
        if error is not None:
            event_payload["error"] = error

        self._append_event("turn_completed", timeline_seq, **event_payload)

        summary_payload = {
            "status": turn_state.status,
            "llm_call_count": turn_state.llm_call_count,
            "tool_call_count": turn_state.tool_call_count,
            "tools_used": turn_state.tools_used,
            "skills_used": turn_state.skills_used,
            "metrics": metrics_summary,
        }
        if error is not None:
            summary_payload["error"] = error

        self._write_timeline_block(
            rule=SECTION_RULE,
            header=f"STEP {timeline_seq:04d} | TURN {turn_state.turn_id:04d} | TURN END",
            timestamp=timestamp,
            sections=self._append_json_block("SUMMARY JSON", summary_payload),
        )

    def _record_error(
        self,
        turn_id: Optional[int],
        phase: str,
        message: str,
        details: Dict[str, Any],
    ) -> None:
        """Write an error block and structured event."""
        self._ensure_initialized()
        timestamp = datetime.now().isoformat()
        timeline_seq = self._next_timeline_seq()
        self._append_event(
            "error",
            timeline_seq,
            turn_id=turn_id,
            phase=phase,
            message=message,
            details=details,
        )
        turn_label = f"TURN {turn_id:04d} | " if turn_id is not None else ""
        self._write_timeline_block(
            rule=SECTION_RULE,
            header=f"STEP {timeline_seq:04d} | {turn_label}ERROR".strip(),
            timestamp=timestamp,
            metadata_lines=[f"Phase: {phase}"],
            sections=self._append_json_block("DETAILS", {"message": message, "details": details}),
        )

    def _prepare_payload_fields(self, payload: Any, turn_id: int, stem: str) -> Dict[str, Any]:
        """Inline or spill a payload depending on serialized size."""
        serialized = self._serialize_payload(payload)
        if len(serialized.encode("utf-8")) <= ARTIFACT_SPILL_THRESHOLD:
            return {"payload": payload}

        artifact_info = self._write_artifact(turn_id=turn_id, stem=stem, payload=payload)
        return {
            "payload_path": artifact_info["path"],
            "payload_bytes": artifact_info["bytes"],
            "payload_format": artifact_info["format"],
        }

    def _write_artifact(self, turn_id: int, stem: str, payload: Any) -> Dict[str, Any]:
        """Persist a large payload to artifacts/ and return its metadata."""
        self._ensure_initialized()
        assert self._artifacts_dir is not None

        payload_format = "txt"
        text = payload if isinstance(payload, str) else self._pretty_json(payload)
        if not isinstance(payload, str):
            payload_format = "json"

        safe_stem = stem.replace("/", "-")
        artifact_name = f"{safe_stem}.{payload_format}"
        artifact_path = self._artifacts_dir / artifact_name
        with self._lock:
            artifact_path.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")

        return {
            "path": str(Path("artifacts") / artifact_name),
            "bytes": len(text.encode("utf-8")),
            "format": payload_format,
        }

    def _serialize_payload(self, payload: Any) -> str:
        """Serialize a payload for size checks."""
        return serialize_payload(payload)

    def _pretty_json(self, payload: Any) -> str:
        """Pretty-print JSON with stable formatting."""
        return pretty_json(payload)

    def _summarize_metrics(self, request_metrics: List[Any]) -> Dict[str, Any]:
        """Aggregate per-request metrics into a turn summary."""
        summary = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
        }
        for metrics in request_metrics:
            summary["prompt_tokens"] += getattr(metrics, "prompt_tokens", 0)
            summary["completion_tokens"] += getattr(metrics, "completion_tokens", 0)
            summary["total_tokens"] += getattr(metrics, "total_tokens", 0)
            summary["cached_tokens"] += getattr(metrics, "cached_tokens", 0)
        return summary

    def _write_fallback_error(self, phase: str, message: str) -> None:
        """Best-effort fallback error writing for logger internals."""
        try:
            self._ensure_initialized()
            self._append_event(
                "error",
                self._next_timeline_seq(),
                turn_id=None,
                phase=phase,
                message=message,
                details={},
            )
        except Exception:
            pass


ChatLogger = SessionLogger
