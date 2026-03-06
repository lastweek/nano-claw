"""Unit tests for HTTP turn event bus lifecycle behavior."""

from time import monotonic

from src.server.event_bus import MAX_CLOSED_TURN_IDS, MAX_QUEUE_SIZE, TurnEventBus


def test_close_tracks_only_bounded_recent_turn_ids():
    """Closed turn tracking should stay bounded in memory."""
    bus = TurnEventBus()
    total_closed = MAX_CLOSED_TURN_IDS + 128

    for index in range(total_closed):
        bus.close(f"turn_{index}")

    assert len(bus._closed_turns) == MAX_CLOSED_TURN_IDS
    assert "turn_0" not in bus._closed_turns
    assert f"turn_{total_closed - 1}" in bus._closed_turns


def test_subscribe_rejects_recently_closed_turn():
    """Subscriptions should be rejected for recently closed turns."""
    bus = TurnEventBus()
    bus.close("turn_closed")

    events = list(bus.subscribe("turn_closed"))

    assert events == []


def test_close_removes_subscriber_and_ends_stream():
    """Closing one turn should drain and end active subscriber iterators."""
    bus = TurnEventBus()
    events_iter = bus.subscribe("turn_live", heartbeat_seconds=60)

    assert "turn_live" in bus._subscribers
    bus.close("turn_live")

    assert list(events_iter) == []
    assert "turn_live" not in bus._subscribers


def test_close_progresses_when_subscriber_queue_is_full():
    """Close should not block when subscriber queue is saturated."""
    bus = TurnEventBus()
    events_iter = bus.subscribe("turn_full", heartbeat_seconds=60)

    # Fill subscriber queue to simulate slow consumers and close backpressure.
    for index in range(MAX_QUEUE_SIZE + 10):
        bus.publish("turn_full", "chunk", {"seq": index})

    start = monotonic()
    bus.close("turn_full")
    duration = monotonic() - start

    assert duration < 1.0
    drained = list(events_iter)
    assert len(drained) <= MAX_QUEUE_SIZE
    assert "turn_full" not in bus._subscribers


def test_close_all_ends_every_active_subscriber():
    """Global shutdown should close subscriber queues across all tracked turns."""
    bus = TurnEventBus()
    events_a = bus.subscribe("turn_a", heartbeat_seconds=60)
    events_b = bus.subscribe("turn_b", heartbeat_seconds=60)

    bus.close_all()

    assert list(events_a) == []
    assert list(events_b) == []
    assert bus._subscribers == {}
