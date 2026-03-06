"""Registry for long-running per-session runtimes."""

from __future__ import annotations

from pathlib import Path
from threading import Event, Lock

from src.config import Config
from src.server.event_bus import TurnEventBus
from src.server.session_resources import SessionResourcesFactory, build_session_resources
from src.server.session_runtime import SessionClosedError, SessionRuntime
from src.store.repository import AppStore


class SessionRegistry:
    """Thread-safe process-local registry of active session runtimes."""

    def __init__(
        self,
        *,
        runtime_config: Config,
        repo_root: Path,
        store: AppStore,
        event_bus: TurnEventBus,
        resources_factory: SessionResourcesFactory = build_session_resources,
    ) -> None:
        self._runtime_config = runtime_config
        self._repo_root = repo_root
        self._store = store
        self._event_bus = event_bus
        self._resources_factory = resources_factory
        self._lock = Lock()
        self._initializing: dict[str, Event] = {}
        self._runtimes: dict[str, SessionRuntime] = {}

    def get_runtime(self, session_id: str) -> SessionRuntime | None:
        """Return an existing runtime without creating one."""
        with self._lock:
            return self._runtimes.get(session_id)

    def ensure_runtime(self, session_id: str) -> SessionRuntime:
        """Return an existing runtime or lazily create one for an active session."""
        self._require_active_session(session_id)

        while True:
            should_build = False
            wait_event: Event | None = None
            with self._lock:
                existing = self._runtimes.get(session_id)
                if existing is not None:
                    return existing
                wait_event = self._initializing.get(session_id)
                if wait_event is None:
                    wait_event = Event()
                    self._initializing[session_id] = wait_event
                    should_build = True

            if should_build:
                break

            wait_event.wait()
            self._require_active_session(session_id)

        runtime: SessionRuntime | None = None
        initialization_event = wait_event
        try:
            runtime = SessionRuntime(
                session_id=session_id,
                runtime_config=self._runtime_config,
                repo_root=self._repo_root,
                store=self._store,
                event_bus=self._event_bus,
                resources_factory=self._resources_factory,
            )
            self._require_active_session(session_id)
            with self._lock:
                existing = self._runtimes.get(session_id)
                if existing is not None:
                    runtime.close()
                    return existing
                self._runtimes[session_id] = runtime
                return runtime
        except Exception:
            if runtime is not None:
                runtime.close()
            raise
        finally:
            with self._lock:
                current_event = self._initializing.get(session_id)
                if current_event is initialization_event:
                    self._initializing.pop(session_id, None)
                    initialization_event.set()

    def close_runtime(self, session_id: str) -> None:
        """Close and remove one runtime if present."""
        with self._lock:
            runtime = self._runtimes.pop(session_id, None)
        if runtime is not None:
            runtime.close()

    def close_all(self) -> None:
        """Close all runtimes and clear the registry."""
        with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            runtime.close()

    def list_runtime_ids(self) -> list[str]:
        """Return session ids that currently have an in-memory runtime."""
        with self._lock:
            return sorted(self._runtimes.keys())

    def snapshot_runtime(self, session_id: str) -> dict | None:
        """Return one runtime snapshot, or None when runtime is not loaded."""
        runtime = self.get_runtime(session_id)
        if runtime is None:
            return None
        return runtime.snapshot()

    def snapshot_all_runtimes(self) -> dict[str, dict]:
        """Return snapshots for all loaded runtimes."""
        with self._lock:
            runtimes = dict(self._runtimes)
        snapshots: dict[str, dict] = {}
        for session_id, runtime in runtimes.items():
            snapshots[session_id] = runtime.snapshot()
        return snapshots

    def _require_active_session(self, session_id: str) -> None:
        session = self._store.get_session_record(session_id)
        if session is None:
            raise KeyError(f"Unknown session: {session_id}")
        if session.state != "active":
            raise SessionClosedError(f"Session is closed: {session_id}")
