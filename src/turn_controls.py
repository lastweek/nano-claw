"""Live turn controls for toggling the running transcript view."""

from __future__ import annotations

import os
import select
import sys
import threading
from typing import IO


class LiveTurnControls:
    """Background key listener for live turn display toggles."""

    def __init__(self, display, *, input_stream: IO[str] | None = None) -> None:
        self.display = display
        self.input_stream = input_stream or sys.stdin
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._original_termios = None

    def start(self) -> bool:
        """Start listening for live control keys when stdin is interactive."""
        if not self._is_interactive_stdin():
            return False

        try:
            import termios
            import tty
        except ImportError:
            return False

        file_descriptor = self.input_stream.fileno()
        self._original_termios = termios.tcgetattr(file_descriptor)
        tty.setcbreak(file_descriptor)
        self._thread = threading.Thread(
            target=self._read_keys_loop,
            name="live-turn-controls",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop listening and restore terminal settings if needed."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
            self._thread = None

        if self._original_termios is not None:
            try:
                import termios

                termios.tcsetattr(
                    self.input_stream.fileno(),
                    termios.TCSADRAIN,
                    self._original_termios,
                )
            except Exception:
                pass
            finally:
                self._original_termios = None

    def handle_key(self, key: str) -> bool:
        """Apply one live key toggle."""
        if key == "v":
            self.display.toggle_mode()
            return True
        if key == "?":
            self.display.toggle_controls_hint()
            return True
        return False

    def _is_interactive_stdin(self) -> bool:
        """Return whether stdin can safely be used for live controls."""
        if os.name == "nt":
            return False

        isatty = getattr(self.input_stream, "isatty", None)
        if not callable(isatty) or not isatty():
            return False

        fileno = getattr(self.input_stream, "fileno", None)
        if not callable(fileno):
            return False

        try:
            fileno()
        except Exception:
            return False
        return True

    def _read_keys_loop(self) -> None:
        """Read keys from stdin until the running turn finishes."""
        file_descriptor = self.input_stream.fileno()
        while not self._stop_event.is_set():
            try:
                ready, _, _ = select.select([file_descriptor], [], [], 0.1)
            except Exception:
                return

            if not ready:
                continue

            try:
                raw_key = os.read(file_descriptor, 1)
            except Exception:
                return

            if not raw_key:
                continue

            try:
                key = raw_key.decode("utf-8", errors="ignore")
            except Exception:
                continue
            self.handle_key(key)
