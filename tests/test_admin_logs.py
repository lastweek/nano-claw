"""Admin log endpoint tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from src.server.app import create_app


def test_admin_log_listing_tail_and_download(temp_dir, http_runtime_config, patch_http_runtime):
    """Admin log endpoints should list files, tail redacted text, and allow raw download."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "logs"}).json()
        log_sessions = client.get(f"/api/v1/admin/log-sessions?session_id={session['id']}").json()
        assert log_sessions["kind"] == "LogSessionList"
        session_dir = Path(log_sessions["items"][0]["spec"]["session_dir"])
        secret_log = session_dir / "secret.log"
        secret_log.write_text("api_key=ABC123SECRET\nline2\n", encoding="utf-8")

        listed = client.get(f"/api/v1/admin/log-files?session_id={session['id']}").json()
        assert listed["kind"] == "LogFileList"
        assert any(item["metadata"]["name"] == "secret.log" for item in listed["items"])

        tailed = client.get(
            f"/api/v1/admin/log-files/tail?session_id={session['id']}&file=secret.log"
        ).json()
        assert tailed["kind"] == "LogFile"
        rendered_tail = "\n".join(tailed["spec"]["tail"])
        assert "***REDACTED***" in rendered_tail

        downloaded = client.get(
            f"/api/v1/admin/log-files/download?session_id={session['id']}&file=secret.log"
        )
        assert downloaded.status_code == 200
        assert downloaded.content.decode("utf-8").startswith("api_key=ABC123SECRET")


def test_admin_log_path_traversal_is_rejected(temp_dir, http_runtime_config, patch_http_runtime):
    """Traversal attempts should fail with 400."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "logs"}).json()
        response = client.get(
            f"/api/v1/admin/log-files?session_id={session['id']}&path=../../etc"
        )
        assert response.status_code == 400


def test_admin_log_tail_does_not_use_path_read_text(
    monkeypatch,
    temp_dir,
    http_runtime_config,
    patch_http_runtime,
):
    """Log tailing should read only the tail window instead of loading full files via read_text."""
    app = create_app(
        runtime_config=http_runtime_config,
        repo_root=temp_dir,
    )

    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"title": "logs"}).json()
        log_sessions = client.get(f"/api/v1/admin/log-sessions?session_id={session['id']}").json()
        session_dir = Path(log_sessions["items"][0]["spec"]["session_dir"])
        large_log = session_dir / "large.log"
        large_log.write_text("\n".join(f"line-{index}" for index in range(2000)), encoding="utf-8")

        monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError))

        tailed = client.get(
            f"/api/v1/admin/log-files/tail?session_id={session['id']}&file=large.log&lines=5"
        )

        assert tailed.status_code == 200
        assert tailed.json()["spec"]["tail"][-1] == "line-1999"
