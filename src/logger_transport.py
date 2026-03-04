"""Async write transport used by SessionLogger."""

from __future__ import annotations

from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable


WriteTask = tuple[Callable[..., None], tuple[Any, ...], dict[str, Any]]


class AsyncWriteTransport:
    """Own the logger's optional background writer thread."""

    def __init__(self, on_error: Callable[[str, str], None]) -> None:
        self._queue: Queue[WriteTask | None] = Queue()
        self._on_error = on_error
        self._thread = Thread(target=self._writer, daemon=True)
        self._thread.start()

    def submit(self, func: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        """Queue a write operation."""
        self._queue.put((func, args, kwargs))

    def close(self, *, timeout: float = 5.0) -> None:
        """Drain queued work and stop the writer thread."""
        self._queue.put(None)
        self._thread.join(timeout=timeout)

    def _writer(self) -> None:
        """Run queued writes in FIFO order."""
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except Empty:
                continue

            if item is None:
                break

            try:
                func, args, kwargs = item
                func(*args, **kwargs)
            except Exception:
                self._on_error("logger.async_writer", "Writer thread error")
