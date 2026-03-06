"""Static UI tests for the local HTTP wrapper."""

from fastapi.testclient import TestClient

from src.server.app import create_app


def test_static_ui_assets_load(temp_dir, http_runtime_config, patch_http_runtime):
    """The root page and static assets should be served when UI mode is enabled."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        index_response = client.get("/")
        assert index_response.status_code == 200
        assert "nano-claw" in index_response.text
        assert "/static/app.js" in index_response.text
        assert "Close Session" in index_response.text

        js_response = client.get("/static/app.js")
        assert js_response.status_code == 200
        assert "EventSource" in js_response.text
        assert "/api/v1/sessions/" in js_response.text
        assert "/turns" in js_response.text

        css_response = client.get("/static/styles.css")
        assert css_response.status_code == 200
        assert ".layout" in css_response.text
