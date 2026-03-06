"""HTTP route tests for session-scoped turn APIs."""

import time

from fastapi.testclient import TestClient

from src.server.app import create_app


def wait_for_turn_completion(client: TestClient, turn_id: str, timeout: float = 2.0) -> dict:
    """Poll the turn endpoint until the turn finishes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/v1/turns/{turn_id}")
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"Turn {turn_id} did not complete")


def test_health_and_session_routes(temp_dir, http_runtime_config, patch_http_runtime):
    """The app should expose health plus session create/list/detail/delete flows."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}

        created = client.post("/api/v1/sessions", json={})
        assert created.status_code == 201
        session_payload = created.json()
        assert session_payload["title"].startswith("Session ")
        assert session_payload["state"] == "active"

        listed = client.get("/api/v1/sessions")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == session_payload["id"]

        detail = client.get(f"/api/v1/sessions/{session_payload['id']}")
        assert detail.status_code == 200
        assert detail.json()["state"] == "active"
        assert detail.json()["busy"] is False

        closed = client.delete(f"/api/v1/sessions/{session_payload['id']}")
        assert closed.status_code == 200
        assert closed.json()["state"] == "closed"

        closed_again = client.delete(f"/api/v1/sessions/{session_payload['id']}")
        assert closed_again.status_code == 200
        assert closed_again.json()["state"] == "closed"


def test_turn_validation_busy_and_detail_endpoints(temp_dir, http_runtime_config, patch_http_runtime):
    """Turn creation should validate inputs and enforce busy/closed semantics."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "HTTP"}).json()

        empty = client.post(f"/api/v1/sessions/{session['id']}/turns", json={"input": "   "})
        assert empty.status_code == 400

        slash = client.post(f"/api/v1/sessions/{session['id']}/turns", json={"input": "/help"})
        assert slash.status_code == 400
        assert slash.json()["detail"] == "Slash commands are CLI-only in HTTP v1."

        queued = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "slow stream"},
        )
        assert queued.status_code == 202
        turn_id = queued.json()["id"]

        busy = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "second turn"},
        )
        assert busy.status_code == 409

        turn_detail = wait_for_turn_completion(client, turn_id)
        assert turn_detail["final_output"] == "Echo: slow stream"

        client.delete(f"/api/v1/sessions/{session['id']}")
        closed = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "after close"},
        )
        assert closed.status_code == 409


def test_two_sessions_are_isolated(temp_dir, http_runtime_config, patch_http_runtime):
    """Two session runtimes should execute independently with isolated history."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session_a = client.post("/api/v1/sessions", json={"title": "A"}).json()
        session_b = client.post("/api/v1/sessions", json={"title": "B"}).json()

        turn_a = client.post(
            f"/api/v1/sessions/{session_a['id']}/turns",
            json={"input": "hello from a"},
        ).json()
        turn_b = client.post(
            f"/api/v1/sessions/{session_b['id']}/turns",
            json={"input": "hello from b"},
        ).json()

        wait_for_turn_completion(client, turn_a["id"])
        wait_for_turn_completion(client, turn_b["id"])

        detail_a = client.get(f"/api/v1/sessions/{session_a['id']}").json()
        detail_b = client.get(f"/api/v1/sessions/{session_b['id']}").json()
        assert [message["content"] for message in detail_a["messages"]] == [
            "hello from a",
            "Echo: hello from a",
        ]
        assert [message["content"] for message in detail_b["messages"]] == [
            "hello from b",
            "Echo: hello from b",
        ]


def test_create_session_rolls_back_persisted_state_when_runtime_init_fails(
    temp_dir,
    http_runtime_config,
):
    """Failed session bootstrap should not leave an active persisted session behind."""
    def failing_resources_factory(*_args, **_kwargs):
        raise RuntimeError("synthetic init failure")

    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
        resources_factory=failing_resources_factory,
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/sessions", json={"title": "broken"})
        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to initialize session runtime: synthetic init failure"
        assert app.state.store.list_sessions() == []
