"""Admin SSE stream tests."""

import asyncio
import json
import threading
import time

from fastapi.testclient import TestClient

from src.server.app import create_app
from src.server.admin_stream import iter_admin_events


def _read_sse_events(response) -> list[tuple[str, dict]]:
    current_event = None
    current_data = None
    events: list[tuple[str, dict]] = []
    for line in response.iter_lines():
        if not line:
            if current_event and current_data is not None:
                events.append((current_event, json.loads(current_data)))
            current_event = None
            current_data = None
            continue
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            current_data = line[6:]
    return events


def test_admin_stream_emits_snapshot_heartbeat_and_resource_changed(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Admin stream should emit snapshot/heartbeat and report changed resources."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        def create_session_later():
            time.sleep(0.35)
            app.state.database.create_session("trigger-change")

        worker = threading.Thread(target=create_session_later, daemon=True)
        worker.start()

        with client.stream(
            "GET",
            "/api/v1/admin/stream?resources=sessions&interval_ms=250&max_events=8",
        ) as response:
            events = _read_sse_events(response)
        worker.join()

        event_names = [name for name, _payload in events]
        assert "snapshot" in event_names
        assert "heartbeat" in event_names
        assert "resource_changed" in event_names


def test_admin_stream_handles_high_event_volume_with_repeated_db_reads(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Admin stream should sustain many snapshot loops without failing."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        def churn_sessions():
            for index in range(12):
                time.sleep(0.2)
                app.state.database.create_session(f"churn-{index}")

        worker = threading.Thread(target=churn_sessions, daemon=True)
        worker.start()

        with client.stream(
            "GET",
            (
                "/api/v1/admin/stream?"
                "resources=overview,sessions,runtimes,turns,event-bus,config"
                "&interval_ms=250&max_events=60"
            ),
        ) as response:
            events = _read_sse_events(response)

        worker.join()
        event_names = [name for name, _payload in events]
        assert len(events) == 60
        assert "snapshot" in event_names
        assert "heartbeat" in event_names
        assert "resource_changed" in event_names


def test_admin_stream_stops_when_client_disconnects(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Admin SSE generator should exit promptly once the client disconnects."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )
    app.state.database.initialize()

    class FakeRequest:
        def __init__(self) -> None:
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls >= 3

    async def collect_events():
        request = FakeRequest()
        stream = iter_admin_events(
            app,
            request=request,
            resources=["overview"],
            session_id=None,
            interval_ms=250,
            max_events=None,
        )
        events = []
        async for event in stream:
            events.append(event)
        return events

    events = asyncio.run(collect_events())
    assert len(events) == 2
    assert events[0].startswith("event: snapshot")
    assert events[1].startswith("event: heartbeat")


def test_admin_stream_stops_when_app_shutdown_is_requested(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Admin SSE generator should exit cleanly once app shutdown starts."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )
    app.state.database.initialize()

    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def collect_events():
        request = FakeRequest()
        stream = iter_admin_events(
            app,
            request=request,
            resources=["overview"],
            session_id=None,
            interval_ms=250,
            max_events=None,
        )
        events = []
        async for event in stream:
            events.append(event)
            if len(events) == 2:
                app.state.shutdown_requested = True
        return events

    events = asyncio.run(collect_events())
    assert len(events) == 2
    assert events[0].startswith("event: snapshot")
    assert events[1].startswith("event: heartbeat")
