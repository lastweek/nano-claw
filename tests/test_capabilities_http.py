"""HTTP/admin tests for capability-request runtime surfaces."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.capabilities import CapabilityRequestManager
from src.server.app import create_app


def test_capability_request_endpoints_and_admin_runtime_snapshot(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Session endpoints and admin runtime snapshots should expose capability requests."""
    app = create_app(runtime_config=http_runtime_config, repo_root=temp_dir)

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "capabilities"}).json()
        runtime = app.state.session_registry.ensure_runtime(session["id"])
        manager = CapabilityRequestManager()
        first = manager.create_or_update(
            summary="Need GitHub tools",
            reason="The task needs GitHub API access.",
            desired_capability="github issue tools",
            request_type="install_extension",
            package_ref="curated:github",
            extension_name="github",
        )
        second = manager.create_or_update(
            summary="Need browser automation",
            reason="The site is JS-heavy.",
            desired_capability="playwright browser automation",
            request_type="generic",
        )
        runtime._resources.capability_request_manager = manager

        list_payload = client.get(f"/api/v1/sessions/{session['id']}/capability-requests").json()
        dismiss_payload = client.post(
            f"/api/v1/sessions/{session['id']}/capability-requests/{first.request_id}/dismiss"
        ).json()
        resolve_payload = client.post(
            f"/api/v1/sessions/{session['id']}/capability-requests/{second.request_id}/resolve"
        ).json()
        admin_payload = client.get(f"/api/v1/admin/runtimes/{session['id']}").json()

    assert list_payload["session_id"] == session["id"]
    assert {item["request_id"] for item in list_payload["requests"]} == {
        first.request_id,
        second.request_id,
    }
    assert dismiss_payload["status"] == "dismissed"
    assert resolve_payload["status"] == "resolved"
    assert admin_payload["status"]["pending_capability_request_count"] == 0
    assert len(admin_payload["spec"]["capability_requests"]) == 2


def test_runtime_reload_resolves_exact_capability_requests(
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Runtime reload should auto-resolve exact pending capability requests."""
    app = create_app(runtime_config=http_runtime_config, repo_root=temp_dir)

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "reload"}).json()
        runtime = app.state.session_registry.ensure_runtime(session["id"])
        manager = CapabilityRequestManager()
        request = manager.create_or_update(
            summary="Need read_file",
            reason="The task needs file reads.",
            desired_capability="read_file",
            request_type="reload_runtime",
            tool_name="read_file",
        )
        runtime._resources.capability_request_manager = manager

        payload = client.post(f"/api/v1/sessions/{session['id']}/runtime/reload").json()

    assert request.request_id in payload["resolved_capability_request_ids"]
    assert payload["pending_capability_request_count"] == 0
