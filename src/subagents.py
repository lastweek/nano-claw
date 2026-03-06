"""Local subagent runtime for delegated child-agent work."""

from __future__ import annotations

import inspect
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Literal, Optional

from src.config import config
from src.context import Context
from src.llm import LLMClient
from src.logger import SessionLogSnapshot, SessionLogger
from src.tools import REQUEST_KIND_SUBAGENT_TURN, clone_tool_registry
from src.turn_activity import TurnActivityEvent
from src.utils import utc_now


def _sanitize_path_fragment(value: str) -> str:
    """Convert a label into the filesystem-safe fragment used by child session dirs."""
    sanitized = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value.strip())
    sanitized = sanitized.strip("-")
    return sanitized or "subagent"


@dataclass(frozen=True)
class SubagentRequest:
    """A parent-authored task brief for a child agent."""

    task: str
    label: str
    context: str
    success_criteria: str
    files: list[str]
    output_hint: str


@dataclass(frozen=True)
class SubagentResult:
    """Structured result returned to the parent agent."""

    subagent_id: str
    label: str
    status: Literal["completed", "failed", "timed_out"]
    summary: str
    report: str
    session_dir: str
    llm_log: str
    events_log: str
    llm_call_count: int
    tool_call_count: int
    tools_used: list[str]
    error: str | None = None

    def to_payload(self) -> dict[str, object]:
        """Serialize the result into the parent tool-result payload."""
        return {
            "subagent_id": self.subagent_id,
            "label": self.label,
            "status": self.status,
            "summary": self.summary,
            "report": self.report,
            "session_dir": self.session_dir,
            "llm_log": self.llm_log,
            "events_log": self.events_log,
            "llm_call_count": self.llm_call_count,
            "tool_call_count": self.tool_call_count,
            "tools_used": self.tools_used,
            "error": self.error,
        }


@dataclass
class SubagentRun:
    """In-memory tracking for a child run in the current parent session."""

    subagent_id: str
    parent_turn_id: int | None
    label: str
    task: str
    status: Literal["running", "completed", "failed", "timed_out"]
    started_at: str
    ended_at: str | None
    duration_s: float | None
    result: SubagentResult | None


@dataclass(frozen=True)
class _PreparedSubagentRun:
    """Main-thread metadata needed before a worker thread launches a child agent."""

    request: SubagentRequest
    run: SubagentRun
    task_message: str
    session_dir: str
    llm_log: str
    events_log: str


class SubagentManager:
    """Create and run local child agents with per-turn-capped fan-out."""

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        max_parallel: Optional[int] = None,
        max_per_turn: Optional[int] = None,
        default_timeout_seconds: Optional[int] = None,
        runtime_config=None,
    ) -> None:
        self.runtime_config = runtime_config or config
        cfg = self.runtime_config.subagents
        self.enabled = enabled if enabled is not None else cfg.enabled
        self.max_parallel = max_parallel if max_parallel is not None else cfg.max_parallel
        self.max_per_turn = max_per_turn if max_per_turn is not None else cfg.max_per_turn
        self.default_timeout_seconds = (
            default_timeout_seconds
            if default_timeout_seconds is not None
            else cfg.default_timeout_seconds
        )

        self._lock = Lock()
        self._run_counter = 0
        self._runs: list[SubagentRun] = []
        self._runs_by_id: dict[str, SubagentRun] = {}
        self._per_turn_counts: dict[tuple[str, int | None], int] = {}

    def list_runs(self) -> list[SubagentRun]:
        """Return runs created in this top-level session."""
        with self._lock:
            return list(self._runs)

    def get_run(self, subagent_id: str) -> SubagentRun | None:
        """Return one run by id."""
        with self._lock:
            return self._runs_by_id.get(subagent_id)

    def build_subagent_request(self, arguments: dict[str, object]) -> SubagentRequest:
        """Normalize tool-call arguments into a subagent request."""
        task = str(arguments.get("task", "")).strip()
        if not task:
            raise ValueError("task is required")

        raw_files = arguments.get("files") or []
        files = (
            [str(item) for item in raw_files if str(item).strip()]
            if isinstance(raw_files, list)
            else []
        )
        return SubagentRequest(
            task=task,
            label=str(arguments.get("label", "")).strip(),
            context=str(arguments.get("context", "")).strip(),
            success_criteria=str(arguments.get("success_criteria", "")).strip(),
            files=files,
            output_hint=str(arguments.get("output_hint", "")).strip(),
        )

    def run_subagents(
        self,
        parent_agent,
        requests: list[SubagentRequest],
        *,
        parent_turn_id: int | None,
        on_event=None,
    ) -> list[SubagentResult]:
        """Run one or many subagents through the same worker-thread path."""
        if not requests:
            raise ValueError("run_subagents requires at least one request")
        if not self.enabled:
            return [self._build_disabled_result(request) for request in requests]

        allowed_count = self._reserve_turn_capacity(
            parent_agent,
            parent_turn_id,
            requested=len(requests),
        )
        allowed_requests = requests[:allowed_count]
        rejected_requests = requests[allowed_count:]

        prepared_subagent_runs = [
            self._create_prepared_subagent_run(parent_agent, request, parent_turn_id)
            for request in allowed_requests
        ]
        results = self._run_prepared_subagents(
            parent_agent,
            prepared_subagent_runs,
            parent_turn_id=parent_turn_id,
            on_event=on_event,
        )
        results.extend(
            self._build_rejected_result(
                request,
                (
                    "Subagent request rejected because this turn exceeded the configured "
                    f"max_per_turn={self.max_per_turn}"
                ),
            )
            for request in rejected_requests
        )
        return results

    def _reserve_turn_capacity(
        self,
        parent_agent,
        parent_turn_id: int | None,
        *,
        requested: int,
    ) -> int:
        """Reserve the remaining per-turn capacity for subagent runs."""
        if parent_turn_id is None:
            return min(requested, self.max_per_turn)

        key = (parent_agent.context.session_id, parent_turn_id)
        with self._lock:
            used = self._per_turn_counts.get(key, 0)
            remaining = max(self.max_per_turn - used, 0)
            allowed_count = min(requested, remaining)
            self._per_turn_counts[key] = used + allowed_count
            return allowed_count

    def _create_prepared_subagent_run(
        self,
        parent_agent,
        request: SubagentRequest,
        parent_turn_id: int | None,
    ) -> _PreparedSubagentRun:
        """Create the stable run record and predictable child log paths for one subagent."""
        subagent_id, label = self._create_subagent_identity(request.label)
        run = self._create_subagent_run_record(
            subagent_id=subagent_id,
            label=label,
            task=request.task,
            parent_turn_id=parent_turn_id,
        )
        session_dir, llm_log, events_log = self._build_child_log_paths(
            parent_agent,
            subagent_id=subagent_id,
            label=label,
        )
        return _PreparedSubagentRun(
            request=request,
            run=run,
            task_message=self._build_subagent_task_message(request),
            session_dir=session_dir,
            llm_log=llm_log,
            events_log=events_log,
        )

    def _create_subagent_identity(self, requested_label: str) -> tuple[str, str]:
        """Create a stable run id and display label."""
        with self._lock:
            self._run_counter += 1
            index = self._run_counter
        label = requested_label.strip() or f"subagent-{index:03d}"
        subagent_id = f"sa_{index:04d}_{uuid.uuid4().hex[:8]}"
        return subagent_id, label

    def _create_subagent_run_record(
        self,
        *,
        subagent_id: str,
        label: str,
        task: str,
        parent_turn_id: int | None,
    ) -> SubagentRun:
        """Create and store the session-tracked record for one subagent run."""
        run = SubagentRun(
            subagent_id=subagent_id,
            parent_turn_id=parent_turn_id,
            label=label,
            task=task,
            status="running",
            started_at=utc_now(),
            ended_at=None,
            duration_s=None,
            result=None,
        )
        with self._lock:
            self._runs.append(run)
            self._runs_by_id[subagent_id] = run
        return run

    def _build_child_log_paths(
        self,
        parent_agent,
        *,
        subagent_id: str,
        label: str,
    ) -> tuple[str, str, str]:
        """Predict the nested child log paths before the worker thread creates its logger."""
        parent_session_dir = parent_agent.logger.ensure_session_dir()
        label_fragment = _sanitize_path_fragment(label)
        id_fragment = _sanitize_path_fragment(subagent_id[:8])
        session_dir = (
            Path(parent_session_dir)
            / "subagents"
            / f"subagent-{label_fragment}-{id_fragment}"
        )
        return (
            str(session_dir),
            str(session_dir / "llm.log"),
            str(session_dir / "events.jsonl"),
        )

    def _build_subagent_task_message(self, request: SubagentRequest) -> str:
        """Render the delegated first user message for a child agent."""
        lines = [
            "You are a delegated subagent working inside the same repository as the parent agent.",
            "Work from this task brief only. Inspect files and use tools yourself rather than assuming parent context.",
            "",
            "Task:",
            request.task,
        ]
        if request.context:
            lines += ["", "Context from parent:", request.context]
        if request.files:
            lines += ["", "Relevant files:"] + [f"- {path}" for path in request.files]
        if request.success_criteria:
            lines += ["", "Success criteria:", request.success_criteria]
        if request.output_hint:
            lines += ["", "Output hint:", request.output_hint]
        lines += ["", "Return a concise report. Put the executive summary in the first paragraph."]
        return "\n".join(lines)

    def _run_prepared_subagents(
        self,
        parent_agent,
        prepared_subagent_runs: list[_PreparedSubagentRun],
        *,
        parent_turn_id: int | None,
        on_event,
    ) -> list[SubagentResult]:
        """Run prepared subagents in worker threads and return results in input order."""
        if not prepared_subagent_runs:
            return []

        for prepared_subagent_run in prepared_subagent_runs:
            self._log_subagent_started(
                parent_agent,
                prepared_subagent_run,
                parent_turn_id=parent_turn_id,
                on_event=on_event,
            )

        results_by_id: dict[str, SubagentResult] = {}
        future_map: dict[object, tuple[_PreparedSubagentRun, float]] = {}

        max_workers = min(self.max_parallel, max(len(prepared_subagent_runs), 1))
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="subagent",
        ) as executor:
            for prepared_subagent_run in prepared_subagent_runs:
                started_at = perf_counter()
                future = executor.submit(
                    self._run_subagent_in_thread,
                    parent_agent,
                    prepared_subagent_run,
                    parent_turn_id,
                    on_event,
                )
                future_map[future] = (prepared_subagent_run, started_at)

            for future, (prepared_subagent_run, started_at) in future_map.items():
                try:
                    result = future.result(timeout=self.default_timeout_seconds)
                except TimeoutError:
                    future.cancel()
                    result = self._build_timed_out_result(
                        prepared_subagent_run,
                        timeout_seconds=self.default_timeout_seconds,
                    )
                except Exception as exc:
                    result = self._build_failed_result(
                        prepared_subagent_run,
                        f"Subagent failed: {exc}",
                    )

                finalized_result = self._finalize_subagent_run_record(
                    prepared_subagent_run.run,
                    result,
                    duration_s=perf_counter() - started_at,
                )
                self._log_subagent_finished(
                    parent_agent,
                    finalized_result,
                    parent_turn_id=parent_turn_id,
                    on_event=on_event,
                )
                results_by_id[prepared_subagent_run.run.subagent_id] = finalized_result

        return [
            results_by_id[prepared_subagent_run.run.subagent_id]
            for prepared_subagent_run in prepared_subagent_runs
        ]

    def _run_subagent_in_thread(
        self,
        parent_agent,
        prepared_subagent_run: _PreparedSubagentRun,
        parent_turn_id: int | None,
        on_event,
    ) -> SubagentResult:
        """Build the child runtime inside the worker thread and return its result."""
        child_logger: SessionLogger | None = None

        try:
            child_context = Context.create(cwd=str(parent_agent.context.cwd))
            child_context.session_mode = parent_agent.context.get_session_mode()
            child_context.current_plan = parent_agent.context.get_current_plan()
            child_context.active_approved_plan_id = parent_agent.context.active_approved_plan_id
            child_logger = self._create_child_logger(
                parent_agent,
                child_context,
                subagent_id=prepared_subagent_run.run.subagent_id,
                label=prepared_subagent_run.run.label,
                parent_turn_id=parent_turn_id,
            )
            child_agent = self._create_child_agent(parent_agent, child_context, child_logger)
            report = child_agent.run(
                prepared_subagent_run.task_message,
                on_event=lambda event: self._forward_subagent_event(
                    event,
                    prepared_subagent_run,
                    on_event,
                ),
            )
        except Exception as exc:
            snapshot = self._close_child_logger(
                child_logger,
                status="error",
            )
            return self._build_failed_result(
                prepared_subagent_run,
                str(exc),
                snapshot=snapshot,
            )

        snapshot = self._close_child_logger(
            child_logger,
            status="completed",
        )
        return self._build_subagent_result(
            prepared_subagent_run,
            snapshot=snapshot,
            status="completed",
            summary=self._extract_summary(report),
            report=report,
        )

    def _forward_subagent_event(
        self,
        event: TurnActivityEvent,
        prepared_subagent_run: _PreparedSubagentRun,
        on_event,
    ) -> None:
        """Forward child activity into the parent turn stream with child worker metadata."""
        if on_event is None:
            return

        on_event(
            TurnActivityEvent(
                kind=event.kind,
                iteration=event.iteration,
                worker_id=prepared_subagent_run.run.subagent_id,
                worker_label=prepared_subagent_run.run.label,
                worker_kind="subagent",
                parent_worker_id="main",
                timestamp=event.timestamp,
                details=dict(event.details),
            )
        )

    def _close_child_logger(
        self,
        child_logger: SessionLogger | None,
        *,
        status: str,
    ) -> SessionLogSnapshot | None:
        """Close a child logger if it exists and return its in-memory snapshot."""
        if child_logger is None:
            return None
        child_logger.close(status=status)
        return child_logger.get_session_snapshot()

    def _create_child_logger(
        self,
        parent_agent,
        child_context: Context,
        *,
        subagent_id: str,
        label: str,
        parent_turn_id: int | None,
    ) -> SessionLogger:
        """Create the nested logger used by one child agent."""
        parent_session_dir = parent_agent.logger.ensure_session_dir()
        child_log_dir = parent_session_dir / "subagents"
        child_logger = SessionLogger(
            child_context.session_id,
            log_dir=str(child_log_dir),
            enabled=parent_agent.logger.enabled,
            async_mode=False,
            update_latest_symlinks=False,
            session_kind="subagent",
            parent_session_id=parent_agent.context.session_id,
            parent_turn_id=parent_turn_id,
            subagent_id=subagent_id,
            subagent_label=label,
            runtime_config=parent_agent.runtime_config,
        )
        parent_llm = parent_agent.llm
        child_logger.start_session(
            cwd=str(child_context.cwd),
            provider=getattr(parent_llm, "provider", "unknown"),
            model=getattr(parent_llm, "model", "unknown"),
            base_url=getattr(parent_llm, "base_url", None),
            streaming_enabled=False,
        )
        child_logger.ensure_session_dir()
        return child_logger

    def _create_child_agent(
        self,
        parent_agent,
        child_context: Context,
        child_logger: SessionLogger,
    ):
        """Create a fresh child agent with inherited normal tools and a fresh context."""
        parent_llm = parent_agent.llm
        llm_kwargs = {
            "provider": getattr(parent_llm, "provider", None),
            "model": getattr(parent_llm, "model", None),
            "base_url": getattr(parent_llm, "base_url", None),
        }
        if "runtime_config" in inspect.signature(LLMClient).parameters:
            llm_kwargs["runtime_config"] = parent_agent.runtime_config
        child_llm = LLMClient(
            **llm_kwargs,
        )
        excluded_tools = set()
        if child_context.get_session_mode() == "plan":
            excluded_tools.update({"write_plan", "submit_plan"})
        excluded_tools.update({"memory_read", "memory_search", "memory_write"})
        child_tools = clone_tool_registry(
            parent_agent.tools,
            include_subagent_tool=False,
            exclude_tools=excluded_tools,
        )

        from src.agent import Agent

        return Agent(
            child_llm,
            child_tools,
            child_context,
            skill_manager=parent_agent.skill_manager,
            logger=child_logger,
            request_kind=REQUEST_KIND_SUBAGENT_TURN,
            runtime_config=parent_agent.runtime_config,
            memory_store=None,
        )

    def _finalize_subagent_run_record(
        self,
        run: SubagentRun,
        result: SubagentResult,
        *,
        duration_s: float | None,
    ) -> SubagentResult:
        """Store final run metadata in memory and return the finalized result."""
        with self._lock:
            run.status = result.status
            run.ended_at = utc_now()
            run.duration_s = duration_s
            run.result = result
        return result

    def _log_subagent_started(
        self,
        parent_agent,
        prepared_subagent_run: _PreparedSubagentRun,
        *,
        parent_turn_id: int | None,
        on_event,
    ) -> None:
        """Record a parent-visible subagent start event."""
        parent_agent.logger.log_subagent_event(
            turn_id=parent_turn_id,
            stage="started",
            subagent_id=prepared_subagent_run.run.subagent_id,
            label=prepared_subagent_run.run.label,
            task=prepared_subagent_run.request.task,
            session_dir=prepared_subagent_run.session_dir,
            llm_log=prepared_subagent_run.llm_log,
            events_log=prepared_subagent_run.events_log,
        )
        if on_event is None:
            return

        on_event(
            TurnActivityEvent(
                kind="subagent_started",
                details={
                    "subagent_id": prepared_subagent_run.run.subagent_id,
                    "label": prepared_subagent_run.run.label,
                    "task": prepared_subagent_run.request.task,
                },
            )
        )

    def _log_subagent_finished(
        self,
        parent_agent,
        result: SubagentResult,
        *,
        parent_turn_id: int | None,
        on_event,
    ) -> None:
        """Record a parent-visible subagent completion or failure event."""
        stage = "completed" if result.status == "completed" else "failed"
        parent_agent.logger.log_subagent_event(
            turn_id=parent_turn_id,
            stage=stage,
            subagent_id=result.subagent_id,
            label=result.label,
            session_dir=result.session_dir,
            llm_log=result.llm_log,
            events_log=result.events_log,
            summary=result.summary,
            error=result.error,
            status=result.status,
        )
        if on_event is None:
            return

        run = self.get_run(result.subagent_id)
        duration_s = run.duration_s if run is not None and run.duration_s is not None else 0.0
        if result.status == "completed":
            on_event(
                TurnActivityEvent(
                    kind="subagent_completed",
                    details={
                        "subagent_id": result.subagent_id,
                        "label": result.label,
                        "duration_s": duration_s,
                        "summary": result.summary,
                    },
                )
            )
            return

        on_event(
            TurnActivityEvent(
                kind="subagent_failed",
                details={
                    "subagent_id": result.subagent_id,
                    "label": result.label,
                    "duration_s": duration_s,
                    "error": result.error or "Subagent failed",
                },
            )
        )

    def _extract_summary(self, report: str) -> str:
        """Derive a concise executive summary from a child report."""
        paragraphs = [paragraph.strip() for paragraph in report.split("\n\n") if paragraph.strip()]
        summary = " ".join(paragraphs[0].split()) if paragraphs else " ".join(report.split())
        if len(summary) > 240:
            summary = summary[:237].rstrip() + "..."
        return summary

    def _build_subagent_result(
        self,
        prepared_subagent_run: _PreparedSubagentRun,
        *,
        snapshot: SessionLogSnapshot | None,
        status: Literal["completed", "failed", "timed_out"],
        summary: str,
        report: str = "",
        error: str | None = None,
    ) -> SubagentResult:
        """Build a structured subagent result from a logger snapshot."""
        return SubagentResult(
            subagent_id=prepared_subagent_run.run.subagent_id,
            label=prepared_subagent_run.run.label,
            status=status,
            summary=summary,
            report=report,
            session_dir=snapshot.session_dir if snapshot is not None else prepared_subagent_run.session_dir,
            llm_log=snapshot.llm_log if snapshot is not None else prepared_subagent_run.llm_log,
            events_log=snapshot.events_log if snapshot is not None else prepared_subagent_run.events_log,
            llm_call_count=snapshot.llm_call_count if snapshot is not None else 0,
            tool_call_count=snapshot.tool_call_count if snapshot is not None else 0,
            tools_used=snapshot.tools_used if snapshot is not None else [],
            error=error,
        )

    def _build_disabled_result(self, request: SubagentRequest) -> SubagentResult:
        """Return a structured failure when subagents are disabled."""
        return SubagentResult(
            subagent_id="disabled",
            label=request.label or "subagent",
            status="failed",
            summary="Subagents are disabled.",
            report="",
            session_dir="",
            llm_log="",
            events_log="",
            llm_call_count=0,
            tool_call_count=0,
            tools_used=[],
            error="Subagents are disabled in the current configuration",
        )

    def _build_rejected_result(self, request: SubagentRequest, message: str) -> SubagentResult:
        """Return a structured failure when the per-turn limit is exceeded."""
        return SubagentResult(
            subagent_id=f"rejected_{uuid.uuid4().hex[:8]}",
            label=request.label or "subagent",
            status="failed",
            summary=message,
            report="",
            session_dir="",
            llm_log="",
            events_log="",
            llm_call_count=0,
            tool_call_count=0,
            tools_used=[],
            error=message,
        )

    def _build_failed_result(
        self,
        prepared_subagent_run: _PreparedSubagentRun,
        message: str,
        *,
        snapshot: SessionLogSnapshot | None = None,
    ) -> SubagentResult:
        """Return a structured failure tied to a prepared child run."""
        return self._build_subagent_result(
            prepared_subagent_run,
            snapshot=snapshot,
            status="failed",
            summary=f"Subagent failed: {message}",
            error=message,
        )

    def _build_timed_out_result(
        self,
        prepared_subagent_run: _PreparedSubagentRun,
        *,
        timeout_seconds: int,
    ) -> SubagentResult:
        """Return a structured timeout result."""
        message = f"Subagent timed out after {timeout_seconds} seconds"
        return self._build_subagent_result(
            prepared_subagent_run,
            snapshot=None,
            status="timed_out",
            summary=message,
            error=message,
        )
