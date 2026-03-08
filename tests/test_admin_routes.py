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
    http_runtime_config.memory.enabled = True
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    class FakeTool:
        def __init__(self, name, description, parameters):
            self.name = name
            self.description = description
            self.parameters = parameters

        def to_schema(self):
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": self.parameters,
                },
            }

    class FakeToolRegistry:
        def __init__(self):
            self._tools = {
                "read_file": FakeTool(
                    "read_file",
                    "Read a file from the workspace.",
                    {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to read."},
                        },
                        "required": ["path"],
                    },
                )
            }

        def list_tools(self):
            return list(self._tools.keys())

        def get(self, name):
            return self._tools.get(name)

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "admin"}).json()
        runtime = app.state.session_registry.get_runtime(session["id"])
        assert runtime is not None
        runtime._resources.tool_registry = FakeToolRegistry()
        app.state.memory_store.upsert_curated_entry(
            session["id"],
            kind="fact",
            title="Deploy",
            content="Run migrations first.",
            reason="seed admin memory",
        )
        app.state.memory_store.append_daily_log(
            session["id"],
            date="2026-03-06",
            title="Daily note",
            content="Checked admin tree.",
            reason="seed daily note",
        )
        memory_entry = app.state.memory_store.list_entries(session["id"])[0]
        app.state.memory_store.record_prompt_injection(
            session["id"],
            turn_id="turn_demo",
            query="deploy",
            policy_name="curated_plus_recent_daily",
            items=[
                app.state.memory_store.build_prompt_memory(session["id"], "deploy").items[0],
            ],
        )
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
        tool_entry = tools["items"][0]["status"]["tools"][0]
        assert "display_name" in tool_entry
        assert "description" in tool_entry
        assert "parameters_schema" in tool_entry
        assert "required_parameters" in tool_entry
        assert "parameter_count" in tool_entry
        assert "function_schema" in tool_entry

        skills = client.get(f"/api/v1/admin/skills?session_id={session['id']}").json()
        assert skills["kind"] == "SkillCatalogStateList"

        mcp = client.get(f"/api/v1/admin/mcp?session_id={session['id']}").json()
        assert mcp["kind"] == "MCPServerStateList"

        subagents = client.get(f"/api/v1/admin/subagents?session_id={session['id']}").json()
        assert subagents["kind"] == "SubagentRunList"

        memory = client.get(f"/api/v1/admin/memory?session_id={session['id']}").json()
        assert memory["kind"] == "SessionMemoryWorkspace"

        memory_document = client.get(f"/api/v1/admin/memory/document?session_id={session['id']}").json()
        assert memory_document["kind"] == "SessionMemoryDocument"
        assert "Run migrations first." in memory_document["status"]["content"]

        memory_entries = client.get(f"/api/v1/admin/memory/entries?session_id={session['id']}").json()
        assert memory_entries["kind"] == "SessionMemoryEntryList"
        assert len(memory_entries["items"]) == 1

        memory_entry_detail = client.get(
            f"/api/v1/admin/memory/entries/{memory_entry.entry_id}?session_id={session['id']}"
        ).json()
        assert memory_entry_detail["kind"] == "SessionMemoryEntry"
        assert memory_entry_detail["status"]["status"] == "active"

        memory_daily = client.get(f"/api/v1/admin/memory/daily?session_id={session['id']}").json()
        assert memory_daily["kind"] == "SessionMemoryDailyLogList"
        assert len(memory_daily["items"]) == 1

        memory_daily_file = client.get(
            f"/api/v1/admin/memory/daily/2026-03-06?session_id={session['id']}"
        ).json()
        assert memory_daily_file["kind"] == "SessionMemoryDailyLog"
        assert "Checked admin tree." in memory_daily_file["status"]["content"]

        memory_settings = client.get(f"/api/v1/admin/memory/settings?session_id={session['id']}").json()
        assert memory_settings["kind"] == "SessionMemorySettings"
        assert memory_settings["status"]["mode"] == "manual_only"
        assert memory_settings["status"]["read_policy"] == "curated_plus_recent_daily"
        assert memory_settings["status"]["prompt_policy"] == "curated_plus_recent_daily"
        assert memory_settings["status"]["debug_enabled"] is False

        memory_audit = client.get(f"/api/v1/admin/memory/audit?session_id={session['id']}").json()
        assert memory_audit["kind"] == "SessionMemoryAuditEventList"
        assert len(memory_audit["items"]) >= 1


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
