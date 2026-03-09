"""Tests for HTTP DB path resolution and legacy migration."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from src.config import Config
from src.server.app import create_app
from src.database.connection import (
    DEFAULT_HTTP_DATABASE_PATH,
    LEGACY_HTTP_DATABASE_PATH,
    migrate_legacy_http_database,
    resolve_http_database_path,
)


def _write_sqlite_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS test (id INTEGER PRIMARY KEY)")


def test_resolve_http_database_path_expands_home_directory(monkeypatch, temp_dir):
    """Default HTTP DB paths should expand the current user's home directory."""
    home_dir = temp_dir / "home"
    repo_root = temp_dir / "repo"
    home_dir.mkdir()
    repo_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    resolved = resolve_http_database_path(DEFAULT_HTTP_DATABASE_PATH, repo_root)

    assert resolved == (home_dir / ".babyclaw" / "state.db").resolve()


def test_create_app_uses_expanded_global_db_path(monkeypatch, temp_dir):
    """App creation should resolve the default HTTP DB path into the user's home directory."""
    home_dir = temp_dir / "home"
    repo_root = temp_dir / "repo"
    home_dir.mkdir()
    repo_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("BABYCLAW_TEST", raising=False)
    monkeypatch.delenv("BABYCLAW_TEST_ROOT", raising=False)
    monkeypatch.delenv("SERVER_DB_PATH", raising=False)

    app = create_app(
        runtime_config=Config({"mcp": {"servers": []}}),
        repo_root=repo_root,
    )

    assert app.state.database.db_path == (home_dir / ".babyclaw" / "state.db").resolve()


def test_migrate_legacy_http_database_moves_main_db_and_sidecars(monkeypatch, temp_dir):
    """Legacy repo-local DB artifacts should move into the global default location."""
    home_dir = temp_dir / "home"
    repo_root = temp_dir / "repo"
    home_dir.mkdir()
    repo_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    legacy_db_path = (repo_root / LEGACY_HTTP_DATABASE_PATH).resolve()
    _write_sqlite_db(legacy_db_path)
    legacy_wal_path = legacy_db_path.with_name(f"{legacy_db_path.name}-wal")
    legacy_shm_path = legacy_db_path.with_name(f"{legacy_db_path.name}-shm")
    legacy_wal_path.write_text("wal", encoding="utf-8")
    legacy_shm_path.write_text("shm", encoding="utf-8")

    global_db_path = resolve_http_database_path(DEFAULT_HTTP_DATABASE_PATH, repo_root)

    notice = migrate_legacy_http_database(global_db_path, repo_root)

    assert notice == f"Migrated legacy repo-local DB from {legacy_db_path} to {global_db_path}."
    assert global_db_path.exists()
    assert global_db_path.with_name(f"{global_db_path.name}-wal").read_text(encoding="utf-8") == "wal"
    assert global_db_path.with_name(f"{global_db_path.name}-shm").read_text(encoding="utf-8") == "shm"
    assert not legacy_db_path.exists()
    assert not legacy_wal_path.exists()
    assert not legacy_shm_path.exists()


def test_migrate_legacy_http_database_skips_existing_global_target(monkeypatch, temp_dir):
    """Legacy migration should not overwrite an existing global DB artifact."""
    home_dir = temp_dir / "home"
    repo_root = temp_dir / "repo"
    home_dir.mkdir()
    repo_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    legacy_db_path = (repo_root / LEGACY_HTTP_DATABASE_PATH).resolve()
    _write_sqlite_db(legacy_db_path)
    global_db_path = resolve_http_database_path(DEFAULT_HTTP_DATABASE_PATH, repo_root)
    global_db_path.parent.mkdir(parents=True, exist_ok=True)
    global_db_path.with_name(f"{global_db_path.name}-wal").write_text("existing", encoding="utf-8")

    notice = migrate_legacy_http_database(global_db_path, repo_root)

    assert notice == (
        "Legacy repo-local DB was left untouched at "
        f"{legacy_db_path} because global DB artifacts already exist at {global_db_path}."
    )
    assert legacy_db_path.exists()
    assert global_db_path.with_name(f"{global_db_path.name}-wal").read_text(encoding="utf-8") == "existing"


def test_migrate_legacy_http_database_ignores_custom_db_path(monkeypatch, temp_dir):
    """Legacy migration should only run for the default global DB target."""
    home_dir = temp_dir / "home"
    repo_root = temp_dir / "repo"
    home_dir.mkdir()
    repo_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    legacy_db_path = (repo_root / LEGACY_HTTP_DATABASE_PATH).resolve()
    _write_sqlite_db(legacy_db_path)
    custom_db_path = resolve_http_database_path("tmp/http.db", repo_root)

    notice = migrate_legacy_http_database(custom_db_path, repo_root)

    assert notice is None
    assert legacy_db_path.exists()
    assert not custom_db_path.exists()


def test_create_app_startup_migrates_legacy_repo_db(monkeypatch, temp_dir):
    """App startup should migrate the legacy repo-local DB before initializing the database."""
    home_dir = temp_dir / "home"
    repo_root = temp_dir / "repo"
    home_dir.mkdir()
    repo_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("BABYCLAW_TEST", raising=False)
    monkeypatch.delenv("BABYCLAW_TEST_ROOT", raising=False)
    monkeypatch.delenv("SERVER_DB_PATH", raising=False)

    legacy_db_path = (repo_root / LEGACY_HTTP_DATABASE_PATH).resolve()
    _write_sqlite_db(legacy_db_path)
    app = create_app(
        runtime_config=Config({"mcp": {"servers": []}}),
        repo_root=repo_root,
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}

    global_db_path = resolve_http_database_path(DEFAULT_HTTP_DATABASE_PATH, repo_root)
    assert global_db_path.exists()
    assert not legacy_db_path.exists()
