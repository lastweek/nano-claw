"""SSE streaming tests for turn APIs."""

import json
import time

from fastapi.testclient import TestClient

from src.server.app import create_app


def read_sse_events(response, stop_events: set[str], *, max_events: int | None = None) -> list[tuple[str, dict]]:
    """Parse SSE lines into `(event, payload)` tuples."""
    current_event = None
    current_data = None
    parsed: list[tuple[str, dict]] = []

    for line in response.iter_lines():
        if not line:
            if current_event and current_data is not None:
                payload = json.loads(current_data)
                parsed.append((current_event, payload))
                if current_event in stop_events:
                    break
                if max_events is not None and len(parsed) >= max_events:
                    break
            current_event = None
            current_data = None
            continue

        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            current_data = line[6:]

    return parsed


def wait_for_terminal_turn(client: TestClient, turn_id: str, timeout: float = 2.0) -> dict:
    """Wait until a turn reaches terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/v1/turns/{turn_id}").json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"Turn {turn_id} did not finish")


def test_sse_stream_emits_live_chunk_and_done_events(temp_dir, http_runtime_config, patch_http_runtime):
    """Active subscribers should receive status, chunks, then done."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "HTTP"}).json()
        turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "slow stream"},
        ).json()

        with client.stream("GET", turn["stream_url"]) as response:
            events = read_sse_events(response, {"done"})

        event_names = [event_name for event_name, _payload in events]
        assert "status" in event_names
        assert "chunk" in event_names
        assert event_names[-1] == "done"


def test_sse_completed_turn_emits_single_terminal_event(temp_dir, http_runtime_config, patch_http_runtime):
    """Connecting after completion should emit one terminal event and close."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "HTTP"}).json()
        turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "hello replay"},
        ).json()
        wait_for_terminal_turn(client, turn["id"])

        with client.stream("GET", turn["stream_url"]) as response:
            events = read_sse_events(response, {"done"})

        event_names = [event_name for event_name, _payload in events]
        assert event_names == ["done"]


def test_sse_disconnect_does_not_stop_turn_execution(temp_dir, http_runtime_config, patch_http_runtime):
    """Disconnecting an SSE client should not cancel active turn execution."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "HTTP"}).json()
        turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "slow stream"},
        ).json()

        with client.stream("GET", turn["stream_url"]) as response:
            events = read_sse_events(response, set(), max_events=2)
            assert events

        terminal = wait_for_terminal_turn(client, turn["id"])
        assert terminal["status"] == "completed"


def test_sse_stream_emits_error_for_failed_turn(temp_dir, http_runtime_config, patch_http_runtime):
    """Failed turns should stream a terminal error event."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "HTTP"}).json()
        turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "cause failure"},
        ).json()

        with client.stream("GET", turn["stream_url"]) as response:
            events = read_sse_events(response, {"error"})

        event_names = [event_name for event_name, _payload in events]
        assert set(event_names).issubset({"status", "error"})
        assert event_names[-1] == "error"
