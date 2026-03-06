"""Session-scoped long-running worker for one HTTP session."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import logging
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from time import monotonic
from typing import Any

from src.config import Config
from src.context import Context
from src.mcp import MCPManager
from src.server.event_bus import TurnEventBus
from src.server.session_resources import (
    SessionResources,
    SessionResourcesFactory,
    build_session_resources,
)
from src.store.repository import AppStore, TurnRecord


class SessionBusyError(Exception):
    """Raised when a session already has an in-flight turn."""


class SessionClosedError(Exception):
    """Raised when a session runtime is closed."""


@dataclass(frozen=True)
class TurnWorkItem:
    """One queued turn work item for a session worker."""

    turn_id: str
    input_text: str


_TURN_STOP = object()
MCP_HEALTH_CACHE_TTL_SECONDS = 5.0
SESSION_RUNTIME_CLOSE_TIMEOUT_SECONDS = 5.0
LOGGER = logging.getLogger(__name__)


class SessionRuntime:
    """One long-lived session runtime with a dedicated worker thread."""

    def __init__(
        self,
        *,
        session_id: str,
        runtime_config: Config,
        repo_root: Path,
        store: AppStore,
        event_bus: TurnEventBus,
        resources_factory: SessionResourcesFactory = build_session_resources,
    ) -> None:
        self.session_id = session_id
        self.runtime_config = runtime_config
        self.repo_root = repo_root
        self.store = store
        self.event_bus = event_bus
        self._resources: SessionResources = resources_factory(
            session_id,
            runtime_config,
            repo_root,
            store,
        )
        self._turn_queue: Queue[TurnWorkItem | object] = Queue()
        self._lock = Lock()
        self._event_seq: dict[str, int] = {}
        self._closed = False
        self._pending_turn_count = 0
        self._active_turn_id: str | None = None
        self._persisted_message_count = len(
            self._persistable_messages(self._resources.context.get_messages())
        )
        self._persisted_compaction_count = self._summary_compaction_count(
            self._resources.context.get_summary()
        )
        self._mcp_health_cache_lock = Lock()
        self._mcp_health_cache: dict[str, bool] = {}
        self._mcp_health_cache_at = 0.0
        self._mcp_health_cache_initialized = False
        self._worker = Thread(
            target=self._worker_loop,
            name=f"nano-claw-session-{session_id}",
            daemon=True,
        )
        self._worker.start()

    def is_busy(self) -> bool:
        with self._lock:
            return self._active_turn_id is not None or self._pending_turn_count > 0

    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def submit_turn(self, input_text: str) -> TurnRecord:
        with self._lock:
            if self._closed:
                raise SessionClosedError(f"Session is closed: {self.session_id}")
            if self._active_turn_id is not None or self._pending_turn_count > 0:
                raise SessionBusyError(f"Session is busy: {self.session_id}")
            turn = self.store.create_turn(self.session_id, input_text)
            self._pending_turn_count += 1
            self._turn_queue.put(TurnWorkItem(turn_id=turn.id, input_text=input_text))

        self._emit_event(turn.id, "status", {"status": "queued"})
        return turn

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._turn_queue.put(_TURN_STOP)
        self._worker.join(timeout=SESSION_RUNTIME_CLOSE_TIMEOUT_SECONDS)
        if self._worker.is_alive():
            LOGGER.warning(
                "Timed out waiting for session runtime %s to stop after %.1fs",
                self.session_id,
                SESSION_RUNTIME_CLOSE_TIMEOUT_SECONDS,
            )

    def snapshot(self) -> dict[str, Any]:
        """Return a best-effort runtime diagnostic snapshot."""
        with self._lock:
            closed = self._closed
            active_turn_id = self._active_turn_id
            pending_turn_count = self._pending_turn_count
            queue_depth = self._turn_queue.qsize()

        context = self._resources.context
        summary = context.get_summary()
        logger_snapshot = self._resources.logger.get_session_snapshot()
        agent = self._resources.agent

        return {
            "session_id": self.session_id,
            "phase": "Closed" if closed else "Running",
            "busy": active_turn_id is not None or pending_turn_count > 0,
            "closed": closed,
            "active_turn_id": active_turn_id,
            "pending_turn_count": pending_turn_count,
            "queue_depth": queue_depth,
            "thread_name": self._worker.name,
            "thread_alive": self._worker.is_alive(),
            "context": {
                "cwd": str(context.cwd),
                "session_mode": context.get_session_mode(),
                "message_count": len(context.get_messages()),
                "summary_present": summary is not None,
                "active_skills": list(context.get_active_skills()),
            },
            "agent": {
                "provider": self.runtime_config.llm.provider,
                "model": self.runtime_config.llm.model,
                "request_metrics": _aggregate_request_metrics(getattr(agent, "request_metrics", [])),
            },
            "logger": {
                "session_dir": logger_snapshot.session_dir,
                "llm_log": logger_snapshot.llm_log,
                "events_log": logger_snapshot.events_log,
                "llm_call_count": logger_snapshot.llm_call_count,
                "tool_call_count": logger_snapshot.tool_call_count,
                "tools_used": logger_snapshot.tools_used,
            },
            "tools": self._build_tool_snapshot(),
            "skills": self._build_skill_snapshot(),
            "mcp": self._build_mcp_snapshot(),
            "subagents": self._build_subagent_snapshot(),
        }

    def _emit_event(self, turn_id: str, event_name: str, payload: dict) -> None:
        with self._lock:
            seq = self._event_seq.get(turn_id, 0) + 1
            self._event_seq[turn_id] = seq
        envelope = {
            "turn_id": turn_id,
            "seq": seq,
            "type": event_name,
            "payload": payload,
        }
        self.event_bus.publish(turn_id, event_name, envelope)

    def _clear_turn_seq(self, turn_id: str) -> None:
        with self._lock:
            self._event_seq.pop(turn_id, None)

    @staticmethod
    def _final_response_from_context(context: Context, fallback: str) -> str:
        messages = context.get_messages()
        if messages and messages[-1]["role"] == "assistant":
            return str(messages[-1]["content"])
        return fallback

    @staticmethod
    def _summary_compaction_count(summary) -> int:
        if summary is None:
            return 0
        return int(getattr(summary, "compaction_count", 0) or 0)

    @staticmethod
    def _persistable_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {
                "role": str(message.get("role", "")),
                "content": str(message.get("content", "")),
            }
            for message in messages
            if message.get("role") in {"user", "assistant"}
        ]

    def _persist_completed_turn_snapshot(self, turn_id: str) -> None:
        summary = self._resources.context.get_summary()
        current_compaction_count = self._summary_compaction_count(summary)
        current_messages = self._persistable_messages(self._resources.context.get_messages())
        current_message_count = len(current_messages)

        # Compaction can rewrite retained history, so append-only persistence is safe only
        # while the transcript still extends the last persisted snapshot.
        should_replace = (
            current_compaction_count > self._persisted_compaction_count
            or current_message_count < self._persisted_message_count
        )

        if should_replace:
            self.store.replace_session_snapshot(self.session_id, turn_id, self._resources.context)
        else:
            try:
                self.store.append_session_snapshot_delta(
                    self.session_id,
                    turn_id,
                    self._resources.context,
                    persisted_message_count=self._persisted_message_count,
                )
            except ValueError:
                self.store.replace_session_snapshot(self.session_id, turn_id, self._resources.context)

        self._persisted_message_count = current_message_count
        self._persisted_compaction_count = current_compaction_count

    def _worker_loop(self) -> None:
        close_status = "completed"
        try:
            while True:
                item = self._turn_queue.get()
                if item is _TURN_STOP:
                    break
                assert isinstance(item, TurnWorkItem)
                turn_id = item.turn_id
                with self._lock:
                    self._pending_turn_count = max(0, self._pending_turn_count - 1)
                    self._active_turn_id = turn_id

                try:
                    self._run_turn(turn_id, item.input_text)
                except Exception as exc:
                    self._finish_turn_failure(
                        turn_id,
                        str(exc) or exc.__class__.__name__,
                    )
                finally:
                    self.event_bus.close(turn_id)
                    self._clear_turn_seq(turn_id)
                    with self._lock:
                        self._active_turn_id = None
        except Exception:
            close_status = "error"
            raise
        finally:
            try:
                self._resources.close(status=close_status)
            except Exception:
                LOGGER.exception("Failed to close session resources for %s", self.session_id)

    def _run_turn(self, turn_id: str, input_text: str) -> None:
        self.store.set_turn_running(turn_id)
        self._emit_event(turn_id, "status", {"status": "running"})
        chunks: list[str] = []
        setattr(self._resources.context, "current_turn_id", turn_id)
        try:
            for chunk in self._resources.agent.run_stream(input_text):
                chunks.append(chunk)
                self._emit_event(turn_id, "chunk", {"text": chunk})
        finally:
            setattr(self._resources.context, "current_turn_id", None)

        final_output = self._final_response_from_context(
            self._resources.context,
            "".join(chunks),
        )
        self._persist_completed_turn_snapshot(turn_id)
        self._finish_turn_success(turn_id, final_output)

    def _finish_turn_success(self, turn_id: str, final_output: str) -> None:
        self.store.finish_turn_success(turn_id, final_output=final_output)
        self._emit_event(turn_id, "done", {"final_output": final_output})

    def _finish_turn_failure(self, turn_id: str, error_text: str) -> None:
        self.store.finish_turn_failure(turn_id, error_text=error_text)
        self._emit_event(turn_id, "error", {"message": error_text})

    def _build_tool_snapshot(self) -> list[dict[str, Any]]:
        tool_registry = self._resources.tool_registry
        if tool_registry is None:
            return []

        tool_state: list[dict[str, Any]] = []
        for name in sorted(tool_registry.list_tools()):
            source = "mcp" if ":" in name else "builtin"
            server = name.split(":", 1)[0] if source == "mcp" and ":" in name else None
            display_name = name.split(":", 1)[1] if server else name
            tool = tool_registry.get(name)

            if tool is None:
                tool_state.append(
                    {
                        "name": name,
                        "display_name": display_name,
                        "source": source,
                        "server": server,
                        "description": "",
                        "parameters_schema": {"type": "object", "properties": {}},
                        "required_parameters": [],
                        "parameter_count": 0,
                        "function_schema": {
                            "name": name,
                            "description": "",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                )
                continue

            schema = tool.to_schema()
            function_schema = deepcopy(schema.get("function") or {})
            description = str(function_schema.get("description") or getattr(tool, "description", "") or "")
            parameters_schema = deepcopy(function_schema.get("parameters") or {})
            if not isinstance(parameters_schema, dict):
                parameters_schema = {}
            properties = parameters_schema.get("properties")
            if not isinstance(properties, dict):
                properties = {}
            parameters_schema.setdefault("type", "object")
            parameters_schema["properties"] = properties
            required_parameters = [
                str(param)
                for param in parameters_schema.get("required", [])
                if str(param)
            ]
            function_schema["name"] = name
            function_schema["description"] = description
            function_schema["parameters"] = deepcopy(parameters_schema)

            tool_state.append(
                {
                    "name": name,
                    "display_name": display_name,
                    "source": source,
                    "server": server,
                    "description": description,
                    "parameters_schema": parameters_schema,
                    "required_parameters": required_parameters,
                    "parameter_count": len(properties),
                    "function_schema": function_schema,
                }
            )
        return tool_state

    def _build_skill_snapshot(self) -> dict[str, Any]:
        skill_manager = self._resources.skill_manager
        if skill_manager is None:
            return {"skills": [], "warnings": []}

        return {
            "skills": [
                {
                    "name": skill.name,
                    "source": skill.source,
                    "catalog_visible": skill.catalog_visible,
                    "body_line_count": skill.body_line_count,
                    "short_description": skill.short_description,
                }
                for skill in skill_manager.list_skills()
            ],
            "warnings": skill_manager.get_warnings(),
        }

    def _build_mcp_snapshot(self) -> dict[str, Any]:
        mcp_manager = self._resources.mcp_manager
        if mcp_manager is None:
            return {"enabled": False, "servers": []}

        health = self._get_cached_mcp_health(mcp_manager)

        return {
            "enabled": True,
            "servers": mcp_manager.list_server_snapshots(health=health),
        }

    def _build_subagent_snapshot(self) -> list[dict[str, Any]]:
        subagent_manager = self._resources.subagent_manager
        if subagent_manager is None:
            return []

        subagent_state: list[dict[str, Any]] = []
        for run in subagent_manager.list_runs():
            summary_text = None
            if run.result is not None:
                summary_text = run.result.summary
            subagent_state.append(
                {
                    "subagent_id": run.subagent_id,
                    "parent_turn_id": run.parent_turn_id,
                    "label": run.label,
                    "task": run.task,
                    "status": run.status,
                    "started_at": run.started_at,
                    "ended_at": run.ended_at,
                    "duration_s": run.duration_s,
                    "summary": summary_text,
                }
            )
        return subagent_state

    def _get_cached_mcp_health(self, mcp_manager: MCPManager) -> dict[str, bool]:
        now = monotonic()
        with self._mcp_health_cache_lock:
            if (
                self._mcp_health_cache_initialized
                and (now - self._mcp_health_cache_at) < MCP_HEALTH_CACHE_TTL_SECONDS
            ):
                return dict(self._mcp_health_cache)

        try:
            raw_status = mcp_manager.get_server_status()
            refreshed_cache = {str(name): bool(status) for name, status in raw_status.items()}
            with self._mcp_health_cache_lock:
                self._mcp_health_cache = refreshed_cache
                self._mcp_health_cache_at = monotonic()
                self._mcp_health_cache_initialized = True
                return dict(self._mcp_health_cache)
        except Exception:
            with self._mcp_health_cache_lock:
                if self._mcp_health_cache_initialized:
                    return dict(self._mcp_health_cache)
            return {}


def _aggregate_request_metrics(metrics: list[Any]) -> dict[str, Any]:
    """Aggregate LLMMetrics-like objects for runtime diagnostics."""
    total_prompt = 0
    total_completion = 0
    total_duration = 0.0
    by_type: dict[str, int] = {}
    for metric in metrics:
        request_type = str(getattr(metric, "request_type", "") or "unknown")
        by_type[request_type] = by_type.get(request_type, 0) + 1
        total_prompt += int(getattr(metric, "prompt_tokens", 0) or 0)
        total_completion += int(getattr(metric, "completion_tokens", 0) or 0)
        total_duration += float(getattr(metric, "duration", 0.0) or 0.0)
    return {
        "count": len(metrics),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "duration_seconds": round(total_duration, 4),
        "request_types": by_type,
    }
