"""HTTP/admin tests for runtime extension surfaces."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

from src.config import Config
from src.server.app import create_app


def write_extension_bundle(repo_root: Path, *, name: str = "http-extension") -> None:
    """Create one minimal repo-local extension bundle."""
    bundle_dir = repo_root / ".babyclaw" / "extensions" / name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "runner.py").write_text(
        "import json, sys\n"
        "payload = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'success': True, 'data': payload.get('tool')}))\n",
        encoding="utf-8",
    )
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": f"{name} description",
        "command": [sys.executable, "runner.py"],
        "tools": [
            {
                "name": "http_extension_tool",
                "description": "HTTP extension tool",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }
        ],
    }
    (bundle_dir / "EXTENSION.yaml").write_text(json.dumps(manifest), encoding="utf-8")
    skill_dir = bundle_dir / "skills" / "http-extension-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: http-extension-skill\ndescription: HTTP extension skill\n---\n\nUse http_extension_tool.\n",
        encoding="utf-8",
    )


def test_admin_extensions_endpoint_lists_discovered_bundles(temp_dir, http_runtime_config, patch_http_runtime):
    """Admin extensions inventory should expose discovered bundle metadata."""
    write_extension_bundle(temp_dir, name="admin-extension")
    app = create_app(runtime_config=http_runtime_config, repo_root=temp_dir)

    with TestClient(app) as client:
        payload = client.get("/api/v1/admin/extensions").json()

    assert payload["kind"] == "ExtensionBundleList"
    assert payload["items"][0]["metadata"]["name"] == "admin-extension"
    assert payload["items"][0]["status"]["tool_count"] == 1


def test_runtime_reload_endpoint_activates_new_extension_tool(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Session runtime reload should pick up a newly added repo-local extension."""
    app = create_app(runtime_config=http_runtime_config, repo_root=temp_dir)
    write_extension_bundle(temp_dir, name="runtime-extension")
    refreshed_config = Config(
        {
            "server": {
                "host": http_runtime_config.server.host,
                "port": http_runtime_config.server.port,
                "db_path": http_runtime_config.server.db_path,
                "max_parallel_runs": http_runtime_config.server.max_parallel_runs,
                "serve_ui": http_runtime_config.server.serve_ui,
                "sse_heartbeat_seconds": http_runtime_config.server.sse_heartbeat_seconds,
            },
            "logging": {
                "enabled": http_runtime_config.logging.enabled,
                "async_mode": http_runtime_config.logging.async_mode,
                "log_dir": http_runtime_config.logging.log_dir,
                "buffer_size": http_runtime_config.logging.buffer_size,
            },
            "memory": {
                "enabled": False,
                "root_dir": http_runtime_config.memory.root_dir,
            },
            "mcp": {"servers": []},
            "extensions": {
                "enabled": True,
                "user_root": str(temp_dir / "user-extensions"),
                "repo_root": ".babyclaw/extensions",
                "runner_timeout_seconds": 1,
                "install_timeout_seconds": 5,
                "catalogs": [],
            },
            "web_tools": {
                "enabled": False,
                "enable_fetch_url": False,
                "enable_read_webpage": False,
                "enable_extract_page_links": False,
            },
            "macos_tools": {"enabled": False},
            "subagents": {"enabled": False},
        }
    )
    app.state.runtime_config_loader = lambda: refreshed_config

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "reload"}).json()
        payload = client.post(f"/api/v1/sessions/{session['id']}/runtime/reload").json()
        tools_payload = client.get(f"/api/v1/admin/tools?session_id={session['id']}").json()
        skills_payload = client.get(f"/api/v1/admin/skills?session_id={session['id']}").json()

    assert "http_extension_tool" in payload["added_tools"]
    assert "http-extension-skill" in payload["added_skills"]
    tool_entry = next(
        tool
        for tool in tools_payload["items"][0]["status"]["tools"]
        if tool["name"] == "http_extension_tool"
    )
    assert tool_entry["source"] == "extension"
    assert tool_entry["extension_name"] == "runtime-extension"
    assert tool_entry["extension_version"] == "1.0.0"
    skill_entry = next(
        skill
        for skill in skills_payload["items"][0]["status"]["skills"]
        if skill["name"] == "http-extension-skill"
    )
    assert skill_entry["source"] == "extension"
    assert skill_entry["extension_name"] == "runtime-extension"
    assert skill_entry["extension_version"] == "1.0.0"


def test_admin_extension_install_endpoint_returns_install_metadata(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
    monkeypatch,
):
    """Admin install endpoint should surface the installed extension metadata."""
    app = create_app(runtime_config=http_runtime_config, repo_root=temp_dir)

    class FakeResult:
        extension = type(
            "Extension",
            (),
            {
                "name": "installed-extension",
                "version": "9.9.9",
                "tool_specs": [object(), object()],
            },
        )()
        install_path = temp_dir / "user-extensions" / "installed-extension"

    monkeypatch.setattr(
        app.state.extension_manager,
        "install_from_catalog",
        lambda package_ref: FakeResult(),
    )

    with TestClient(app) as client:
        payload = client.post(
            "/api/v1/admin/extensions/install",
            json={"package": "curated:installed-extension"},
        ).json()

    assert payload["package"] == "curated:installed-extension"
    assert payload["name"] == "installed-extension"
    assert payload["version"] == "9.9.9"
    assert payload["tool_count"] == 2
