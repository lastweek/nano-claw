"""Static admin UI tests."""

from fastapi.testclient import TestClient

from src.server.app import create_app


def test_admin_ui_assets_load(temp_dir, http_runtime_config, patch_http_runtime):
    """Admin root and static assets should load."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        index_response = client.get("/admin")
        assert index_response.status_code == 200
        assert "nano-claw admin" in index_response.text
        assert "/admin/static/app.js" in index_response.text
        assert 'id="admin-nav"' in index_response.text
        assert 'id="tree-view"' in index_response.text
        assert 'id="detail-header"' in index_response.text
        assert 'id="detail-tabs"' in index_response.text
        assert 'id="detail-content"' in index_response.text
        assert 'id="connection-status"' in index_response.text

        js_response = client.get("/admin/static/app.js")
        assert js_response.status_code == 200
        assert "EventSource" in js_response.text
        assert "/api/v1/admin/stream" in js_response.text
        assert "SESSION_CHILD_NODE_DEFS" in js_response.text
        assert "skill-item" in js_response.text
        assert "tool-item" in js_response.text
        assert "extractSkillCatalog" in js_response.text
        assert "extractToolCatalog" in js_response.text
        assert "renderSkillsSummary" in js_response.text
        assert "renderToolsSummary" in js_response.text
        assert "renderToolItemSummary" in js_response.text
        assert "renderToolParameterTable" in js_response.text
        assert "function_schema" in js_response.text
        assert "parameters_schema" in js_response.text
        assert "required_parameters" in js_response.text
        assert "evictMissingSession" in js_response.text
        assert "removeNodeSubtree" in js_response.text
        assert "error.status = response.status" in js_response.text
        assert '"Memory"' in js_response.text
        assert "memory-document" in js_response.text
        assert "memory-daily-list" in js_response.text
        assert "renderMemoryWorkspaceSummary" in js_response.text
        assert '"Context"' in js_response.text
        assert '"Runtime"' in js_response.text
        assert '"Tools"' in js_response.text
        assert '"MCP"' in js_response.text
        assert '"Turns"' in js_response.text
        assert '"Logs"' in js_response.text

        css_response = client.get("/admin/static/styles.css")
        assert css_response.status_code == 200
        assert ".tree-view" in css_response.text
        assert ".admin-nav" in css_response.text
        assert ".tree-row" in css_response.text
