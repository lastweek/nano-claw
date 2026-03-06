"""Admin route integration tests."""

import time

from fastapi.testclient import TestClient

from src.server.app import create_app


def _wait_for_turn(client: TestClient, turn_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/v1/turns/{turn_id}").json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"Turn {turn_id} did not finish")


def test_admin_overview_and_resource_endpoints(temp_dir, http_runtime_config, patch_http_runtime):
    """Admin routes should expose list/detail resources with K8s-style kinds."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )
    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "admin"}).json()
        turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "hello admin"},
        ).json()
        _wait_for_turn(client, turn["id"])

        overview = client.get("/api/v1/admin/overview")
        assert overview.status_code == 200
        assert overview.json()["kind"] == "ServerOverview"

        sessions = client.get("/api/v1/admin/sessions").json()
        assert sessions["kind"] == "SessionList"

        runtimes = client.get("/api/v1/admin/runtimes").json()
        assert runtimes["kind"] == "SessionRuntimeList"

        turns = client.get("/api/v1/admin/turns").json()
        assert turns["kind"] == "TurnList"

        tools = client.get(f"/api/v1/admin/tools?session_id={session['id']}").json()
        assert tools["kind"] == "ToolRegistryStateList"

        skills = client.get(f"/api/v1/admin/skills?session_id={session['id']}").json()
        assert skills["kind"] == "SkillCatalogStateList"

        mcp = client.get(f"/api/v1/admin/mcp?session_id={session['id']}").json()
        assert mcp["kind"] == "MCPServerStateList"

        subagents = client.get(f"/api/v1/admin/subagents?session_id={session['id']}").json()
        assert subagents["kind"] == "SubagentRunList"


def test_admin_runtime_busy_and_event_bus_subscribers(temp_dir, http_runtime_config, patch_http_runtime):
    """Runtime endpoint should show busy states and event-bus subscriber counts."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )
    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "admin"}).json()
        turn = client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"input": "slow stream"},
        ).json()

        busy_seen = False
        deadline = time.time() + 1.5
        while time.time() < deadline:
            runtime = client.get(f"/api/v1/admin/runtimes/{session['id']}").json()
            if runtime["status"]["busy"]:
                busy_seen = True
                break
            time.sleep(0.01)
        assert busy_seen

        with client.stream("GET", f"/api/v1/turns/{turn['id']}/stream") as response:
            # Start consuming one line so the SSE handler is active.
            _ = next(response.iter_lines())
            event_bus = client.get("/api/v1/admin/event-bus").json()
            counts = event_bus["status"]["subscriber_counts"]
            assert isinstance(counts, dict)
            assert "closed_turn_count" in event_bus["status"]
        _wait_for_turn(client, turn["id"])


def test_admin_routes_are_read_only(temp_dir, http_runtime_config, patch_http_runtime):
    """Admin APIs should reject mutating verbs."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/admin/overview", json={})
        assert response.status_code == 405
