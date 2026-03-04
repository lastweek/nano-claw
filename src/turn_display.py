"""Grouped live CLI transcript for one running turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from time import perf_counter
from typing import Any, Literal, Optional

from rich.console import Group, RenderableType
from rich.text import Text

from src.activity_preview import build_tool_signature
from src.context import Context
from src.statusline import build_rich_statusline
from src.turn_activity import TurnActivityEvent

MAIN_AGENT_LABEL_STYLE = "bold #7dd3fc"
SUBAGENT_LABEL_STYLE = "bold #fdba74"


@dataclass
class TranscriptEntry:
    """One visible entry inside a worker transcript section."""

    kind: Literal["llm_call", "tool_call", "subagent_dispatch", "completion", "error", "status"]
    title: str
    status: Literal["running", "finished", "failed", "timed_out"]
    duration_s: float | None
    summary: str
    body_label: str | None = None
    body_text: str | None = None


@dataclass
class WorkerTranscript:
    """Live transcript state for one worker section."""

    worker_id: str
    label: str
    worker_kind: Literal["main", "subagent"]
    parent_worker_id: str | None
    status: Literal["running", "waiting", "finished", "failed", "timed_out"]
    phase: str
    started_at: float | None
    updated_at: float | None
    llm_call_count: int = 0
    tool_call_count: int = 0
    subagent_count: int = 0
    completed_entries: list[TranscriptEntry] = field(default_factory=list)
    active_entry: TranscriptEntry | None = None


@dataclass
class TurnLiveViewState:
    """Grouped live transcript view state."""

    mode: Literal["simple", "verbose"]
    detail_mode: Literal["collapsed", "expanded"]
    show_controls_hint: bool
    worker_order: list[str]
    workers: dict[str, WorkerTranscript]


class TurnProgressDisplay:
    """Track and render grouped user-visible turn activity."""

    def __init__(
        self,
        *,
        session_context: Context | None = None,
        skill_debug: bool = False,
        request_type: str = "streaming",
        live_activity_mode: Literal["simple", "verbose"] = "simple",
        live_activity_details: Literal["collapsed", "expanded"] = "collapsed",
    ) -> None:
        self.summary_lines: list[str] = []
        self.response_chunks: list[str] = []
        self.streaming_started = False
        self.skill_debug = skill_debug
        self.request_type = request_type
        self.session_context = session_context
        self.turn_started_at = perf_counter()
        self._lock = RLock()
        self.live_state = TurnLiveViewState(
            mode=live_activity_mode,
            detail_mode=live_activity_details,
            show_controls_hint=False,
            worker_order=["main"],
            workers={
                "main": WorkerTranscript(
                    worker_id="main",
                    label="Main Agent",
                    worker_kind="main",
                    parent_worker_id=None,
                    status="running",
                    phase="preparing",
                    started_at=self.turn_started_at,
                    updated_at=self.turn_started_at,
                )
            },
        )

    def __rich__(self) -> RenderableType:
        """Render the live grouped transcript when mounted in Rich Live."""
        return self.render_live()

    def handle_event(self, event: TurnActivityEvent) -> None:
        """Update display state from one activity event."""
        with self._lock:
            worker = self._get_or_create_worker(event)
            worker.updated_at = event.timestamp
            if worker.started_at is None:
                worker.started_at = event.timestamp

            self._update_worker_state(worker, event)
            self._apply_live_event(worker, event)

            summary_line = self._format_summary_line(event)
            if summary_line and (not self.summary_lines or self.summary_lines[-1] != summary_line):
                self.summary_lines.append(summary_line)

    def append_stream_chunk(self, token: str) -> None:
        """Append one streamed answer chunk."""
        if not token:
            return

        if not self.streaming_started:
            self.streaming_started = True
            self.handle_event(TurnActivityEvent(kind="answer_stream_started"))

        with self._lock:
            self.response_chunks.append(token)

    def toggle_mode(self) -> None:
        """Toggle simple/verbose live rendering."""
        with self._lock:
            self.live_state.mode = "verbose" if self.live_state.mode == "simple" else "simple"

    def toggle_controls_hint(self) -> None:
        """Toggle the controls hint footer."""
        with self._lock:
            self.live_state.show_controls_hint = not self.live_state.show_controls_hint

    def render_live(self) -> RenderableType:
        """Render the grouped live transcript."""
        with self._lock:
            now = perf_counter()
            renderables: list[RenderableType] = []

            for worker_id in self.live_state.worker_order:
                worker = self.live_state.workers[worker_id]
                renderables.append(self._render_worker(worker, now))
                renderables.append(Text(""))

            if self.live_state.show_controls_hint:
                renderables.append(
                    Text("v: simple/verbose  ?: controls", style="dim")
                )
            if self.session_context is not None:
                renderables.append(
                    build_rich_statusline(
                        self.session_context,
                        view_mode=self.live_state.mode,
                    )
                )
            elif renderables:
                renderables.pop()

            return Group(*renderables) if renderables else Text("")

    def render_persisted(self) -> RenderableType:
        """Render the concise persisted summary."""
        with self._lock:
            if not self.summary_lines:
                return Text("")

            return Group(*[Text(f"  • {line}", style="dim") for line in self.summary_lines])

    def final_response_text(self) -> str:
        """Return the accumulated streamed response text."""
        with self._lock:
            return "".join(self.response_chunks)

    def has_summary(self) -> bool:
        """Whether there is summary output to persist."""
        with self._lock:
            return bool(self.summary_lines)

    def _get_or_create_worker(self, event: TurnActivityEvent) -> WorkerTranscript:
        """Return the worker transcript for one event, creating it if needed."""
        worker = self.live_state.workers.get(event.worker_id)
        if worker is None:
            worker = WorkerTranscript(
                worker_id=event.worker_id,
                label=event.worker_label or "Worker",
                worker_kind=event.worker_kind,
                parent_worker_id=event.parent_worker_id,
                status="running",
                phase="starting",
                started_at=event.timestamp,
                updated_at=event.timestamp,
            )
            self.live_state.workers[event.worker_id] = worker
            self.live_state.worker_order.append(event.worker_id)
        return worker

    def _ensure_subagent_worker_from_main_event(self, event: TurnActivityEvent) -> None:
        """Create a child section early when the main agent dispatches a subagent."""
        subagent_id = event.details.get("subagent_id")
        label = event.details.get("label")
        if not subagent_id or not label or subagent_id in self.live_state.workers:
            return

        worker = WorkerTranscript(
            worker_id=subagent_id,
            label=label,
            worker_kind="subagent",
            parent_worker_id="main",
            status="running",
            phase="starting",
            started_at=event.timestamp,
            updated_at=event.timestamp,
        )
        self.live_state.workers[subagent_id] = worker
        self.live_state.worker_order.append(subagent_id)

    def _update_worker_state(self, worker: WorkerTranscript, event: TurnActivityEvent) -> None:
        """Update the section header state for one event."""
        iteration = event.iteration + 1 if event.iteration is not None else None
        details = event.details

        if event.kind == "skill_preload":
            worker.phase = "preparing skills"
            return
        if event.kind == "plan_mode_entered":
            worker.phase = "planning"
            worker.status = "running"
            return
        if event.kind == "plan_written":
            worker.phase = "planning"
            return
        if event.kind == "plan_submitted":
            worker.phase = "awaiting plan review"
            return
        if event.kind == "plan_approved":
            worker.phase = "plan approved"
            return
        if event.kind == "plan_rejected":
            worker.phase = "plan rejected"
            return
        if event.kind == "plan_execution_started":
            worker.phase = "executing approved plan"
            worker.status = "running"
            return
        if event.kind == "plan_cleared":
            worker.phase = "build"
            return
        if event.kind == "context_compaction_started":
            worker.phase = "compacting context"
            worker.status = "running"
            return
        if event.kind == "context_compaction_completed":
            worker.phase = "thinking"
            return
        if event.kind == "context_compaction_failed":
            worker.phase = "context compaction failed"
            return
        if event.kind == "skill_load_requested":
            worker.phase = "loading skills"
            return
        if event.kind == "skill_load_succeeded":
            worker.phase = "thinking"
            return
        if event.kind == "skill_load_failed":
            worker.phase = "skill load failed"
            return
        if event.kind == "llm_call_started":
            worker.phase = "thinking"
            worker.status = "running"
            if iteration is not None:
                worker.llm_call_count = max(worker.llm_call_count, iteration)
            return
        if event.kind == "llm_call_finished":
            if details.get("has_tool_calls"):
                worker.phase = "using tools"
            else:
                worker.phase = "final answer ready"
            if iteration is not None:
                worker.llm_call_count = max(worker.llm_call_count, iteration)
            return
        if event.kind == "tool_call_started":
            worker.phase = "using tools"
            worker.status = "running"
            worker.tool_call_count += 1
            return
        if event.kind == "tool_call_finished":
            worker.phase = "thinking"
            return
        if event.kind == "subagent_started":
            worker.phase = "running subagents"
            worker.subagent_count += 1
            self._ensure_subagent_worker_from_main_event(event)
            return
        if event.kind == "subagent_completed":
            worker.phase = "thinking"
            child = self.live_state.workers.get(details.get("subagent_id"))
            if child is not None:
                child.status = "finished"
                child.phase = "completed"
            return
        if event.kind == "subagent_failed":
            worker.phase = "subagent failed"
            child = self.live_state.workers.get(details.get("subagent_id"))
            if child is not None:
                child.status = "failed"
                child.phase = "failed"
            return
        if event.kind == "answer_stream_started":
            worker.phase = "streaming answer"
            return
        if event.kind == "turn_completed":
            worker.status = "finished" if details.get("status") == "completed" else "failed"
            worker.phase = "completed" if worker.status == "finished" else "failed"
            return
        if event.kind == "turn_error":
            worker.status = "failed"
            worker.phase = "error"

    def _apply_live_event(self, worker: WorkerTranscript, event: TurnActivityEvent) -> None:
        """Apply one event to the grouped live transcript."""
        if event.kind == "llm_call_started":
            self._start_active_entry(worker, self._build_running_llm_entry(event))
            return

        if event.kind == "llm_call_finished":
            self._finish_active_entry(worker, "llm_call", self._build_finished_llm_entry(event))
            return

        if event.kind == "tool_call_started":
            if event.details.get("tool_name") == "run_subagent":
                return
            self._start_active_entry(worker, self._build_running_tool_entry(event))
            return

        if event.kind == "tool_call_finished":
            if event.details.get("tool_name") == "run_subagent":
                return
            self._finish_active_entry(worker, "tool_call", self._build_finished_tool_entry(event))
            return

        if event.kind == "subagent_started":
            worker.completed_entries.append(self._build_subagent_dispatch_entry(event))
            return

        if event.kind == "subagent_completed":
            worker.completed_entries.append(self._build_subagent_completion_entry(event))
            child = self.live_state.workers.get(event.details.get("subagent_id"))
            if child is not None:
                child.completed_entries.append(self._build_subagent_child_completion_entry(event))
                child.active_entry = None
            return

        if event.kind == "subagent_failed":
            worker.completed_entries.append(self._build_subagent_failure_entry(event))
            child = self.live_state.workers.get(event.details.get("subagent_id"))
            if child is not None:
                child.completed_entries.append(self._build_subagent_child_failure_entry(event))
                child.active_entry = None
            return

        standalone_entry = self._build_standalone_entry(event)
        if standalone_entry is not None:
            worker.completed_entries.append(standalone_entry)

    def _build_running_llm_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the active LLM-call entry."""
        iteration = event.iteration + 1 if event.iteration is not None else "?"
        return TranscriptEntry(
            kind="llm_call",
            title=f"LLM call {iteration} running",
            status="running",
            duration_s=None,
            summary=f"LLM call {iteration} running",
        )

    def _build_finished_llm_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the finalized LLM-call entry."""
        iteration = event.iteration + 1 if event.iteration is not None else "?"
        details = event.details
        duration = float(details.get("duration_s", 0.0))
        has_tool_calls = details.get("has_tool_calls", False)
        tool_call_count = details.get("tool_call_count", 0)

        if has_tool_calls:
            tool_word = "tool" if tool_call_count == 1 else "tools"
            title = f"LLM call {iteration} requested {tool_call_count} {tool_word}"
        else:
            title = f"LLM call {iteration} produced final answer"

        assistant_body = details.get("assistant_body") or ""
        body_label = None
        if assistant_body:
            body_label = (
                "requested tools"
                if has_tool_calls and assistant_body.lstrip().startswith("- ")
                else "assistant response"
            )

        return TranscriptEntry(
            kind="llm_call",
            title=title,
            status="finished",
            duration_s=duration,
            summary=title,
            body_label=body_label,
            body_text=assistant_body or None,
        )

    def _build_running_tool_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the active tool-call entry."""
        signature = build_tool_signature(
            event.details.get("tool_name"),
            event.details.get("arguments"),
        )
        return TranscriptEntry(
            kind="tool_call",
            title=f"Tool {signature} running",
            status="running",
            duration_s=None,
            summary=f"Tool {signature} running",
        )

    def _build_finished_tool_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the finalized tool-call entry."""
        details = event.details
        duration = float(details.get("duration_s", 0.0))
        signature = build_tool_signature(details.get("tool_name"), details.get("arguments"))

        if details.get("success", True):
            title = f"Tool {signature} finished ({duration:.2f}s)"
            status: Literal["finished", "failed", "timed_out"] = "finished"
        else:
            title = f"Tool {signature} failed ({duration:.2f}s)"
            status = "failed"

        return TranscriptEntry(
            kind="tool_call",
            title=title,
            status=status,
            duration_s=duration,
            summary=title,
            body_label="result" if details.get("result_body") else None,
            body_text=details.get("result_body") or None,
        )

    def _build_subagent_dispatch_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the main-agent dispatch milestone for one child run."""
        label = event.details.get("label", "subagent")
        return TranscriptEntry(
            kind="subagent_dispatch",
            title=f"Spawned subagent: {label}",
            status="finished",
            duration_s=None,
            summary=f"Spawned subagent: {label}",
            body_label="task" if event.details.get("task") else None,
            body_text=event.details.get("task") or None,
        )

    def _build_subagent_completion_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the main-agent completion milestone for one child run."""
        label = event.details.get("label", "subagent")
        duration = float(event.details.get("duration_s", 0.0))
        return TranscriptEntry(
            kind="completion",
            title=f"Subagent finished: {label} ({duration:.2f}s)",
            status="finished",
            duration_s=duration,
            summary=f"Subagent finished: {label}",
            body_label="summary" if event.details.get("summary") else None,
            body_text=event.details.get("summary") or None,
        )

    def _build_subagent_failure_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the main-agent failure milestone for one child run."""
        label = event.details.get("label", "subagent")
        duration = float(event.details.get("duration_s", 0.0))
        return TranscriptEntry(
            kind="error",
            title=f"Subagent failed: {label} ({duration:.2f}s)",
            status="failed",
            duration_s=duration,
            summary=f"Subagent failed: {label}",
            body_label="error" if event.details.get("error") else None,
            body_text=event.details.get("error") or None,
        )

    def _build_subagent_child_completion_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the child-section completion entry."""
        return TranscriptEntry(
            kind="completion",
            title="Completed delegated work",
            status="finished",
            duration_s=float(event.details.get("duration_s", 0.0)),
            summary="Completed delegated work",
            body_label="summary" if event.details.get("summary") else None,
            body_text=event.details.get("summary") or None,
        )

    def _build_subagent_child_failure_entry(self, event: TurnActivityEvent) -> TranscriptEntry:
        """Create the child-section failure entry."""
        return TranscriptEntry(
            kind="error",
            title="Delegated work failed",
            status="failed",
            duration_s=float(event.details.get("duration_s", 0.0)),
            summary="Delegated work failed",
            body_label="error" if event.details.get("error") else None,
            body_text=event.details.get("error") or None,
        )

    def _build_standalone_entry(self, event: TurnActivityEvent) -> TranscriptEntry | None:
        """Build a completed entry for one standalone live event."""
        if event.kind == "skill_preload":
            title = f"Skill preloaded: {event.details.get('skill_name')} ({event.details.get('reason')})"
            return TranscriptEntry("status", title, "finished", None, title)

        if event.kind == "plan_mode_entered":
            title = f"Entered planning mode for: {event.details.get('task')}"
            return TranscriptEntry("status", title, "finished", None, title)

        if event.kind == "plan_written":
            title = f"Plan artifact updated: {event.details.get('file_path')}"
            return TranscriptEntry("status", title, "finished", None, title)

        if event.kind == "plan_submitted":
            title = "Plan submitted for review"
            return TranscriptEntry(
                "status",
                title,
                "finished",
                None,
                title,
                body_label="summary" if event.details.get("summary") else None,
                body_text=event.details.get("summary"),
            )

        if event.kind == "plan_approved":
            title = "Plan approved"
            return TranscriptEntry("status", title, "finished", None, title)

        if event.kind == "plan_rejected":
            title = "Plan rejected"
            return TranscriptEntry("status", title, "failed", None, title)

        if event.kind == "plan_execution_started":
            title = "Started executing approved plan"
            return TranscriptEntry("status", title, "running", None, title)

        if event.kind == "plan_cleared":
            title = "Cleared approved plan contract"
            return TranscriptEntry("status", title, "finished", None, title)

        if event.kind == "context_compaction_started":
            title = f"Compacting context: {event.details.get('covered_turn_count', 0)} older turns"
            return TranscriptEntry("status", title, "running", None, title)

        if event.kind == "context_compaction_completed":
            title = (
                f"Context compacted: {event.details.get('covered_turn_count', 0)} turns summarized, "
                f"{event.details.get('retained_turn_count', 0)} recent turns kept"
            )
            self._remove_last_running_status_entry(event.worker_id)
            return TranscriptEntry("status", title, "finished", None, title)

        if event.kind == "context_compaction_failed":
            self._remove_last_running_status_entry(event.worker_id)
            title = f"Context compaction failed: {event.details.get('error')}"
            return TranscriptEntry(
                "error",
                title,
                "failed",
                None,
                title,
                body_label="error",
                body_text=event.details.get("error"),
            )

        if event.kind == "turn_error":
            title = f"Turn failed: {event.details.get('message')}"
            return TranscriptEntry(
                "error",
                title,
                "failed",
                None,
                title,
                body_label="error",
                body_text=event.details.get("message"),
            )

        return None

    def _remove_last_running_status_entry(self, worker_id: str) -> None:
        """Drop the last temporary running status entry for a worker if it exists."""
        worker = self.live_state.workers.get(worker_id)
        if worker is None or not worker.completed_entries:
            return
        last_entry = worker.completed_entries[-1]
        if last_entry.kind == "status" and last_entry.status == "running":
            worker.completed_entries.pop()

    def _start_active_entry(self, worker: WorkerTranscript, entry: TranscriptEntry) -> None:
        """Replace the current active entry for a worker."""
        worker.active_entry = entry

    def _finish_active_entry(
        self,
        worker: WorkerTranscript,
        expected_kind: Literal["llm_call", "tool_call"],
        entry: TranscriptEntry,
    ) -> None:
        """Finalize the active entry for one worker."""
        if worker.active_entry is not None and worker.active_entry.kind == expected_kind:
            worker.active_entry = None
        worker.completed_entries.append(entry)

    def _format_summary_line(self, event: TurnActivityEvent) -> Optional[str]:
        """Map one event to a concise persisted summary line."""
        if event.worker_kind == "subagent":
            return None

        iteration = event.iteration + 1 if event.iteration is not None else None
        details = event.details

        if event.kind == "skill_preload":
            line = f"Skill preloaded: {details.get('skill_name')} ({details.get('reason')})"
            if self.skill_debug:
                line += (
                    f" [source={details.get('source')}, "
                    f"catalog={'yes' if details.get('catalog_visible') else 'no'}]"
                )
            return line

        if event.kind == "plan_mode_entered":
            return f"Entered planning mode for: {details.get('task')}"

        if event.kind == "plan_written":
            return f"Plan updated: {details.get('file_path')}"

        if event.kind == "plan_submitted":
            return "Plan submitted for review"

        if event.kind == "plan_approved":
            return "Plan approved"

        if event.kind == "plan_rejected":
            return "Plan rejected"

        if event.kind == "plan_execution_started":
            return "Started executing approved plan"

        if event.kind == "plan_cleared":
            return "Cleared approved plan contract"

        if event.kind == "context_compaction_completed":
            return f"Context compacted: {details.get('covered_turn_count', 0)} older turns summarized"

        if event.kind == "context_compaction_failed":
            return f"Context compaction failed: {details.get('error')}"

        if event.kind == "skill_load_succeeded":
            return f"Loaded skill: {details.get('skill_name')}"

        if event.kind == "skill_load_failed":
            line = f"Failed to load skill: {details.get('skill_name')}"
            if self.skill_debug and details.get("error"):
                line += f" ({details.get('error')})"
            return line

        if event.kind == "llm_call_finished":
            if details.get("has_tool_calls"):
                tool_call_count = details.get("tool_call_count", 0)
                tool_word = "tool" if tool_call_count == 1 else "tools"
                return f"LLM call {iteration} requested {tool_call_count} {tool_word}"
            return f"LLM call {iteration} produced final answer"

        if event.kind == "tool_call_finished":
            signature = build_tool_signature(details.get("tool_name"), details.get("arguments"))
            duration = float(details.get("duration_s", 0.0))
            if details.get("success", True):
                return f"Tool finished: {signature} ({duration:.2f}s)"
            line = f"Tool failed: {signature} ({duration:.2f}s)"
            if self.skill_debug and details.get("error"):
                line += f" ({details.get('error')})"
            return line

        if event.kind == "subagent_completed":
            return f"Subagent finished: {details.get('label')}"

        if event.kind == "subagent_failed":
            line = f"Subagent failed: {details.get('label')} ({float(details.get('duration_s', 0.0)):.2f}s)"
            if self.skill_debug and details.get("error"):
                line += f" ({details.get('error')})"
            return line

        if event.kind == "turn_error":
            return f"Turn failed: {details.get('message')}"

        return None

    def _render_worker(self, worker: WorkerTranscript, now: float) -> RenderableType:
        """Render one worker section."""
        renderables: list[RenderableType] = [
            self._build_worker_header_text(worker, now)
        ]

        entries = list(worker.completed_entries)
        if worker.active_entry is not None:
            entries.append(worker.active_entry)

        for index, entry in enumerate(entries, start=1):
            renderables.extend(self._render_entry(entry, index))

        return Group(*renderables)

    def _build_worker_header_text(self, worker: WorkerTranscript, now: float) -> Text:
        """Render the section header text for one worker."""
        label = worker.label if worker.worker_kind == "main" else f"Subagent: {worker.label}"
        elapsed = self._format_elapsed(now - (worker.started_at or now))
        label_style = self._worker_label_style(worker)
        header = Text()
        header.append(label, style=label_style)
        header.append(
            f"  [{worker.status} • {worker.phase} • {elapsed}]",
            style="dim",
        )
        return header

    def _worker_label_style(self, worker: WorkerTranscript) -> str:
        """Return the visual style for one worker label."""
        if worker.worker_kind == "main":
            return MAIN_AGENT_LABEL_STYLE
        return SUBAGENT_LABEL_STYLE

    def _render_entry(self, entry: TranscriptEntry, index: int) -> list[RenderableType]:
        """Render one entry in simple or verbose mode."""
        style = "dim"
        if entry.status == "running":
            style = "yellow"
        elif entry.status == "failed":
            style = "red"
        elif entry.status == "timed_out":
            style = "red"

        renderables: list[RenderableType] = [Text(f"  {index}. {entry.title}", style=style)]

        if self.live_state.mode != "verbose":
            return renderables

        if not entry.body_label or not entry.body_text or entry.status == "running":
            return renderables

        if self.live_state.detail_mode == "collapsed":
            renderables.append(Text(f"     {entry.body_label}: folded", style="dim"))
            return renderables

        renderables.append(Text(f"     {entry.body_label}:", style="dim"))
        for line in entry.body_text.splitlines():
            renderables.append(Text(f"       {line}", style="dim"))
        return renderables

    def _format_elapsed(self, seconds: float) -> str:
        """Format elapsed time for a section header."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        remainder = seconds - (minutes * 60)
        return f"{minutes}m {remainder:.0f}s"
