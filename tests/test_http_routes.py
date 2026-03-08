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


def test_delete_session_removes_unified_session_directory(temp_dir, http_runtime_config, patch_http_runtime):
    """Deleting a session should remove its shared on-disk session directory."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "Cleanup Me"}).json()
        session_dir = app.state.memory_store.session_root(session["id"])
        app.state.memory_store.ensure_curated_document(session["id"])

        turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "hello"},
        ).json()
        wait_for_turn_completion(client, turn["id"])
        assert session_dir.exists()

        closed = client.delete(f"/api/v1/sessions/{session['id']}")
        assert closed.status_code == 200
        assert not session_dir.exists()


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
        assert app.state.database.list_sessions() == []


def test_memory_routes_expose_file_backed_session_memory(temp_dir, http_runtime_config, patch_http_runtime):
    """Memory endpoints should expose raw docs plus structured entries and settings."""
    http_runtime_config.memory.enabled = True
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "memory"}).json()

        workspace = client.get(f"/api/v1/sessions/{session['id']}/memory")
        assert workspace.status_code == 200
        assert workspace.json()["daily_files"] == []
        assert workspace.json()["entry_count"] == 0
        assert workspace.json()["settings_mode"] == "manual_only"
        assert workspace.json()["settings_read_policy"] == "curated_plus_recent_daily"
        assert workspace.json()["settings_prompt_policy"] == "curated_plus_recent_daily"
        assert workspace.json()["debug_enabled"] is False

        document = client.get(f"/api/v1/sessions/{session['id']}/memory/document")
        assert document.status_code == 200
        assert "# Session Memory" in document.json()["content"]

        settings = client.get(f"/api/v1/sessions/{session['id']}/memory/settings")
        assert settings.status_code == 200
        assert settings.json()["mode"] == "manual_only"
        assert settings.json()["read_policy"] == "curated_plus_recent_daily"
        assert settings.json()["prompt_policy"] == "curated_plus_recent_daily"
        assert settings.json()["debug_enabled"] is False

        settings_patch = client.patch(
            f"/api/v1/sessions/{session['id']}/memory/settings",
            json={
                "mode": "auto",
                "read_policy": "curated_only",
                "prompt_policy": "search_all_ranked",
            },
        )
        assert settings_patch.status_code == 200
        assert settings_patch.json()["mode"] == "auto"
        assert settings_patch.json()["read_policy"] == "curated_only"
        assert settings_patch.json()["prompt_policy"] == "search_all_ranked"

        created_entry = client.post(
            f"/api/v1/sessions/{session['id']}/memory/entries",
            json={
                "kind": "fact",
                "title": "deploy-order",
                "content": "Run migrations first.",
                "reason": "seed entry",
                "source": "http_api",
                "confidence": 0.9,
            },
        )
        assert created_entry.status_code == 201
        entry_id = created_entry.json()["id"]

        listed_entries = client.get(f"/api/v1/sessions/{session['id']}/memory/entries")
        assert listed_entries.status_code == 200
        assert listed_entries.json()["entries"][0]["id"] == entry_id

        archived_entry = client.patch(
            f"/api/v1/sessions/{session['id']}/memory/entries/{entry_id}",
            json={"action": "archive", "reason": "archive it"},
        )
        assert archived_entry.status_code == 200
        assert archived_entry.json()["status"] == "archived"

        deleted_entry = client.delete(f"/api/v1/sessions/{session['id']}/memory/entries/{entry_id}")
        assert deleted_entry.status_code == 204

        updated = client.put(
            f"/api/v1/sessions/{session['id']}/memory/document",
            json={"content": "# Session Memory\n\n## Facts\n\n### Deploy\n\nShip carefully.\n"},
        )
        assert updated.status_code == 200
        assert "Ship carefully." in updated.json()["content"]

        appended = client.post(
            f"/api/v1/sessions/{session['id']}/memory/daily/2026-03-06",
            json={"title": "Build note", "content": "Investigated memory flow."},
        )
        assert appended.status_code == 201
        assert "Build note" in appended.json()["content"]

        listed = client.get(f"/api/v1/sessions/{session['id']}/memory/daily")
        assert listed.status_code == 200
        assert listed.json()["files"][0]["date"] == "2026-03-06"

        searched = client.get(
            f"/api/v1/sessions/{session['id']}/memory/search",
            params={"q": "memory", "include_daily": True, "include_inactive": True},
        )
        assert searched.status_code == 200
        assert len(searched.json()["hits"]) >= 1
        assert searched.json()["hits"][0]["title"]


def test_memory_routes_return_clear_error_when_disabled(temp_dir, http_runtime_config, patch_http_runtime):
    """Memory endpoints should fail cleanly when the feature is disabled."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "no-memory"}).json()
        response = client.get(f"/api/v1/sessions/{session['id']}/memory")
        assert response.status_code == 400
        assert response.json()["detail"] == "Session memory is disabled."
        entry_response = client.get(f"/api/v1/sessions/{session['id']}/memory/entries")
        assert entry_response.status_code == 400
