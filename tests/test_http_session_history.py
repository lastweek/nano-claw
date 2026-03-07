"""HTTP persistence tests focused on session transcript continuity."""

import time

from fastapi.testclient import TestClient

from src.server.app import create_app
from src.database.session_database import SessionDatabase


def wait_for_turn(client: TestClient, turn_id: str, timeout: float = 2.0) -> dict:
    """Poll until one turn reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/v1/turns/{turn_id}").json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"Turn {turn_id} did not finish")


def test_http_session_snapshot_matches_compacted_context(temp_dir, http_runtime_config, patch_http_runtime):
    """Persisted session history should be replaced by compacted context snapshots."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "HTTP"}).json()
        first_turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "hello one"},
        ).json()
        wait_for_turn(client, first_turn["id"])

        compact_turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "compact session"},
        ).json()
        wait_for_turn(client, compact_turn["id"])

        session_detail = client.get(f"/api/v1/sessions/{session['id']}").json()
        assert session_detail["summary_text"] == "Compacted summary"
        assert [message["content"] for message in session_detail["messages"]] == [
            "compact session",
            "Echo: compact session",
        ]


def test_server_startup_marks_incomplete_turns_failed(temp_dir, http_runtime_config, patch_http_runtime):
    """Creating the app should fail stale queued/running turns from prior processes."""
    database = SessionDatabase(temp_dir / "state.db")
    database.initialize()
    session = database.create_session("Restart")
    queued = database.create_turn(session.id, "queued")
    running = database.create_turn(session.id, "running")
    database.set_turn_running(running.id)

    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )
    with TestClient(app):
        pass

    assert database.get_turn(queued.id).status == "failed"
    assert database.get_turn(running.id).status == "failed"


def test_cold_restart_lazy_runtime_rehydration(temp_dir, http_runtime_config, patch_http_runtime):
    """Active sessions should recreate runtime lazily after process restart."""
    app_first = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )
    with TestClient(app_first) as client:
        created = client.post("/api/v1/sessions", json={"title": "persisted"}).json()
        session_id = created["id"]

    app_second = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )
    with TestClient(app_second) as client:
        assert app_second.state.session_registry.get_runtime(session_id) is None
        turn = client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={"input": "hello again"},
        )
        assert turn.status_code == 202
        wait_for_turn(client, turn.json()["id"])
        assert app_second.state.session_registry.get_runtime(session_id) is not None


def test_http_non_compaction_turns_use_append_snapshot_path(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Regular turns should persist via append delta without full snapshot replace."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    append_calls = 0
    replace_calls = 0
    original_append = app.state.database.append_session_snapshot_delta
    original_replace = app.state.database.replace_session_snapshot

    def append_wrapper(*args, **kwargs):
        nonlocal append_calls
        append_calls += 1
        return original_append(*args, **kwargs)

    def replace_wrapper(*args, **kwargs):
        nonlocal replace_calls
        replace_calls += 1
        return original_replace(*args, **kwargs)

    app.state.database.append_session_snapshot_delta = append_wrapper
    app.state.database.replace_session_snapshot = replace_wrapper

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "HTTP"}).json()
        first_turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "first"},
        ).json()
        wait_for_turn(client, first_turn["id"])

        second_turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "second"},
        ).json()
        wait_for_turn(client, second_turn["id"])

    assert append_calls == 2
    assert replace_calls == 0


def test_http_compaction_turn_uses_replace_snapshot_path(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Compaction rewrites should fall back to full snapshot replacement."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    append_calls = 0
    replace_calls = 0
    original_append = app.state.database.append_session_snapshot_delta
    original_replace = app.state.database.replace_session_snapshot

    def append_wrapper(*args, **kwargs):
        nonlocal append_calls
        append_calls += 1
        return original_append(*args, **kwargs)

    def replace_wrapper(*args, **kwargs):
        nonlocal replace_calls
        replace_calls += 1
        return original_replace(*args, **kwargs)

    app.state.database.append_session_snapshot_delta = append_wrapper
    app.state.database.replace_session_snapshot = replace_wrapper

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "HTTP"}).json()
        first_turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "first"},
        ).json()
        wait_for_turn(client, first_turn["id"])

        compact_turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "compact session"},
        ).json()
        wait_for_turn(client, compact_turn["id"])

    assert append_calls >= 1
    assert replace_calls >= 1
