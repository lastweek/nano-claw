"""In-memory live event fan-out for HTTP turn streaming."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Lock
from time import time
from typing import Iterator


MAX_SUBSCRIBERS_PER_TURN = 10
MAX_QUEUE_SIZE = 100
DEFAULT_HEARTBEAT_SECONDS = 10
MAX_CLOSED_TURN_IDS = 4096


@dataclass(frozen=True)
class SSEEvent:
    """One live event emitted to SSE subscribers."""

    event: str
    data: dict


_SENTINEL = object()


class TurnEventBus:
    """Thread-safe in-memory event bus keyed by turn id."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: dict[str, list[Queue]] = {}
        self._closed_turns: set[str] = set()
        self._closed_turn_order: deque[str] = deque()

    def publish(self, turn_id: str, event_name: str, payload: dict) -> None:
        """Publish one event to current subscribers."""
        with self._lock:
            subscribers = list(self._subscribers.get(turn_id, []))
        event = SSEEvent(event=event_name, data=payload)
        for subscriber in subscribers:
            try:
                subscriber.put(event, block=False)
            except Full:
                pass

    def subscribe(self, turn_id: str, *, heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS) -> Iterator[SSEEvent]:
        """Yield live events for one turn until it is closed."""
        queue: Queue = Queue(maxsize=MAX_QUEUE_SIZE)
        with self._lock:
            if turn_id in self._closed_turns:
                return iter(())
            subscribers = self._subscribers.get(turn_id, [])
            if len(subscribers) >= MAX_SUBSCRIBERS_PER_TURN:
                return iter(())
            subscribers.append(queue)
            self._subscribers[turn_id] = subscribers

        def iterator() -> Iterator[SSEEvent]:
            try:
                while True:
                    try:
                        item = queue.get(timeout=heartbeat_seconds)
                    except Empty:
                        yield SSEEvent(
                            event="heartbeat",
                            data={
                                "turn_id": turn_id,
                                "type": "heartbeat",
                                "payload": {"timestamp": time()},
                            },
                        )
                        continue

                    if item is _SENTINEL:
                        return
                    yield item
            finally:
                self._remove_subscriber(turn_id, queue)

        return iterator()

    def close(self, turn_id: str) -> None:
        """Close and drain all subscribers for one turn."""
        with self._lock:
            subscribers = self._subscribers.pop(turn_id, [])
            if turn_id not in self._closed_turns:
                self._closed_turns.add(turn_id)
                self._closed_turn_order.append(turn_id)
            while len(self._closed_turn_order) > MAX_CLOSED_TURN_IDS:
                oldest_turn_id = self._closed_turn_order.popleft()
                self._closed_turns.discard(oldest_turn_id)
        for subscriber in subscribers:
            self._signal_close(subscriber)

    def close_all(self) -> None:
        """Close and drain every subscriber across all turns."""
        with self._lock:
            subscriber_groups = list(self._subscribers.values())
            self._subscribers.clear()
        for subscribers in subscriber_groups:
            for subscriber in subscribers:
                self._signal_close(subscriber)

    def snapshot(self) -> dict:
        """Return an in-memory diagnostic snapshot for admin views."""
        with self._lock:
            subscriber_counts = {
                turn_id: len(queues)
                for turn_id, queues in self._subscribers.items()
            }
            tracked_turn_ids = sorted(self._subscribers.keys())
            closed_turn_ids = sorted(self._closed_turns)

        return {
            "tracked_turn_ids": tracked_turn_ids,
            "subscriber_counts": subscriber_counts,
            "closed_turn_count": len(closed_turn_ids),
            "closed_turn_ids": closed_turn_ids,
            "max_subscribers_per_turn": MAX_SUBSCRIBERS_PER_TURN,
            "max_queue_size": MAX_QUEUE_SIZE,
            "default_heartbeat_seconds": DEFAULT_HEARTBEAT_SECONDS,
        }

    def _remove_subscriber(self, turn_id: str, queue: Queue) -> None:
        with self._lock:
            subscribers = self._subscribers.get(turn_id)
            if not subscribers:
                return
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                self._subscribers.pop(turn_id, None)

    @staticmethod
    def _signal_close(queue: Queue) -> None:
        while True:
            try:
                queue.put(_SENTINEL, block=False)
                return
            except Full:
                # Drop backlog rather than block runtime shutdown; the sentinel must win.
                try:
                    queue.get_nowait()
                except Empty:
                    continue
