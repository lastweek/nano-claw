"""Collector-level tests for admin resource assembly."""

import time

from fastapi.testclient import TestClient

from src.server.admin_collectors_core import (
    collect_event_bus_state,
    collect_server_overview,
    collect_sessions,
    collect_turns,
)
from src.server.admin_collectors_runtime import (
    collect_agent_runtime,
    collect_mcp,
    collect_skills,
    collect_tools,
)
from src.server.app import create_app
from src.server.session_runtime import MCP_HEALTH_CACHE_TTL_SECONDS


def _wait_for_turn(client: TestClient, turn_id: str, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/v1/turns/{turn_id}").json()
        if payload["status"] in {"completed", "failed"}:
            return
        time.sleep(0.01)
    raise AssertionError(f"Turn {turn_id} did not complete")


def test_collect_overview_sessions_and_turns(temp_dir, http_runtime_config, patch_http_runtime):
    """Collectors should emit Kubernetes-style resources with expected kinds."""
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

        overview = collect_server_overview(app)
        assert overview["kind"] == "ServerOverview"
        assert overview["status"]["session_count"] >= 1
        assert "turn_status_counts" in overview["status"]

        sessions = collect_sessions(app)
        assert sessions["kind"] == "SessionList"
        assert sessions["items"]

        turns = collect_turns(
            app,
            session_id=session["id"],
            status=None,
            cursor=None,
            limit=10,
            redacted=True,
        )
        assert turns["kind"] == "TurnList"
        assert turns["items"][0]["kind"] == "Turn"


def test_collect_agent_runtime_and_event_bus(temp_dir, http_runtime_config, patch_http_runtime):
    """Runtime and event-bus collectors should surface live internals."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "admin"}).json()

        runtime = collect_agent_runtime(app, session["id"])
        assert runtime["kind"] == "AgentRuntime"
        assert runtime["status"]["phase"] in {"Running", "Closed"}

        event_bus = collect_event_bus_state(app)
        assert event_bus["kind"] == "EventBusState"
        assert "subscriber_counts" in event_bus["status"]


def test_collect_mcp_uses_cached_health_with_ttl_and_stale_fallback(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """MCP health collection should be cached and fall back to stale cache on errors."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    class FakeMCPManager:
        def __init__(self) -> None:
            self.calls = 0
            self.fail = False

        def get_server_status(self) -> dict[str, bool]:
            self.calls += 1
            if self.fail:
                raise RuntimeError("health failure")
            return {"deepwiki": True}

        def list_server_snapshots(self, *, health: dict[str, bool] | None = None):
            current_health = bool((health or {}).get("deepwiki", False))
            return [
                {
                    "name": "deepwiki",
                    "url": "https://mcp.deepwiki.com/mcp",
                    "protocol_version": "2024-11-05",
                    "session_id_present": True,
                    "initialized": True,
                    "health": current_health,
                    "cached_tool_count": 1,
                }
            ]

        def close_all(self) -> None:
            return

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "admin"}).json()
        runtime = app.state.session_registry.get_runtime(session["id"])
        assert runtime is not None

        fake_mcp = FakeMCPManager()
        runtime._resources.mcp_manager = fake_mcp
        runtime._mcp_health_cache_initialized = False
        runtime._mcp_health_cache = {}
        runtime._mcp_health_cache_at = 0.0

        first = collect_mcp(app, session["id"])
        second = collect_mcp(app, session["id"])
        assert first["kind"] == "MCPServerStateList"
        assert second["kind"] == "MCPServerStateList"
        assert fake_mcp.calls == 1

        runtime._mcp_health_cache_at = time.monotonic() - (MCP_HEALTH_CACHE_TTL_SECONDS + 0.1)
        refreshed = collect_mcp(app, session["id"])
        assert refreshed["kind"] == "MCPServerStateList"
        assert fake_mcp.calls == 2

        fake_mcp.fail = True
        runtime._mcp_health_cache_at = time.monotonic() - (MCP_HEALTH_CACHE_TTL_SECONDS + 0.1)
        stale = collect_mcp(app, session["id"])
        assert stale["kind"] == "MCPServerStateList"
        assert fake_mcp.calls == 3
        servers = stale["items"][0]["status"]["servers"]
        assert servers and servers[0]["health"] is True


def test_collect_skills_and_tools_expose_runtime_entries(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Skill/tool collectors should expose the entry fields the admin UI drills into."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    class FakeSkillManager:
        def list_skills(self):
            return [
                type(
                    "SkillDef",
                    (),
                    {
                        "name": "pdf",
                        "source": "repo",
                        "catalog_visible": True,
                        "body_line_count": 42,
                        "short_description": "Use for PDF tasks",
                    },
                )(),
                type(
                    "SkillDef",
                    (),
                    {
                        "name": "db",
                        "source": "user",
                        "catalog_visible": True,
                        "body_line_count": 12,
                        "short_description": "Database helper",
                    },
                )(),
            ]

        def get_warnings(self):
            return ["missing optional reference"]

    class FakeToolRegistry:
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

        def __init__(self):
            self._tools = {
                "read_file": self.FakeTool(
                    "read_file",
                    "Read a file from the workspace.",
                    {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path to read."},
                            "offset": {"type": "integer", "description": "Start line."},
                        },
                        "required": ["path"],
                    },
                ),
                "deepwiki:ask_question": self.FakeTool(
                    "deepwiki:ask_question",
                    "Ask DeepWiki about a repository. (via deepwiki)",
                    {
                        "type": "object",
                        "properties": {
                            "repoName": {"type": "string", "description": "owner/repo"},
                            "question": {"type": "string", "description": "Question"},
                        },
                        "required": ["repoName", "question"],
                    },
                ),
            }

        def list_tools(self):
            return list(self._tools.keys())

        def get(self, name):
            return self._tools.get(name)

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "admin"}).json()
        runtime = app.state.session_registry.get_runtime(session["id"])
        assert runtime is not None

        runtime._resources.context.active_skills = ["pdf"]
        runtime._resources.skill_manager = FakeSkillManager()
        runtime._resources.tool_registry = FakeToolRegistry()

        skills = collect_skills(app, session["id"])
        assert skills["kind"] == "SkillCatalogStateList"
        skill_status = skills["items"][0]["status"]
        assert skill_status["active_skills"] == ["pdf"]
        assert skill_status["warnings"] == ["missing optional reference"]
        assert skill_status["skills"][0]["name"] == "pdf"
        assert skill_status["skills"][0]["source"] == "repo"
        assert skill_status["skills"][0]["catalog_visible"] is True
        assert skill_status["skills"][0]["body_line_count"] == 42
        assert skill_status["skills"][0]["short_description"] == "Use for PDF tasks"

        tools = collect_tools(app, session["id"])
        assert tools["kind"] == "ToolRegistryStateList"
        tool_status = tools["items"][0]["status"]
        assert tool_status["tool_count"] == 2

        mcp_tool = tool_status["tools"][0]
        assert mcp_tool["name"] == "deepwiki:ask_question"
        assert mcp_tool["display_name"] == "ask_question"
        assert mcp_tool["source"] == "mcp"
        assert mcp_tool["server"] == "deepwiki"
        assert mcp_tool["description"] == "Ask DeepWiki about a repository. (via deepwiki)"
        assert mcp_tool["required_parameters"] == ["repoName", "question"]
        assert mcp_tool["parameter_count"] == 2
        assert mcp_tool["parameters_schema"]["properties"]["repoName"]["type"] == "string"
        assert mcp_tool["function_schema"]["name"] == "deepwiki:ask_question"
        assert mcp_tool["function_schema"]["parameters"]["required"] == ["repoName", "question"]

        builtin_tool = tool_status["tools"][1]
        assert builtin_tool["name"] == "read_file"
        assert builtin_tool["display_name"] == "read_file"
        assert builtin_tool["source"] == "builtin"
        assert builtin_tool["server"] is None
        assert builtin_tool["description"] == "Read a file from the workspace."
        assert builtin_tool["required_parameters"] == ["path"]
        assert builtin_tool["parameter_count"] == 2
        assert builtin_tool["parameters_schema"]["properties"]["path"]["description"] == "Path to read."
        assert builtin_tool["function_schema"]["parameters"]["properties"]["offset"]["type"] == "integer"


def test_collect_tools_for_not_loaded_runtime_returns_empty_catalog(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Tool collector should preserve the not-loaded contract for unknown runtimes."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app):
        tools = collect_tools(app, "sess_missing")
        assert tools["kind"] == "ToolRegistryStateList"
        assert tools["items"][0]["status"]["phase"] == "NotLoaded"
        assert tools["items"][0]["status"]["tool_count"] == 0
        assert tools["items"][0]["status"]["tools"] == []
