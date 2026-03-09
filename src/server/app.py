"""FastAPI application factory for the local HTTP wrapper."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import Config, config
from src.extensions import ExtensionManager
from src.memory import SessionMemory
from src.server.admin_routes import router as admin_router
from src.server.event_bus import TurnEventBus
from src.server.routes import router
from src.server.session_registry import SessionRegistry
from src.server.session_resources import SessionResourcesFactory, build_session_resources
from src.database.connection import migrate_legacy_http_database, resolve_http_database_path
from src.database.session_database import SessionDatabase
from src.utils import resolve_path


def create_app(
    *,
    runtime_config: Config | None = None,
    repo_root: Path | None = None,
    resources_factory: SessionResourcesFactory | None = None,
) -> FastAPI:
    """Create the local FastAPI app for one repo-root daemon instance."""
    resolved_config = runtime_config or config
    resolved_repo_root = resolve_path(repo_root or Path.cwd())
    db_path = resolve_http_database_path(resolved_config.server.db_path, resolved_repo_root)

    database = SessionDatabase(db_path)
    memory_store = SessionMemory(
        repo_root=resolved_repo_root,
        runtime_config=resolved_config,
        session_lookup=database.get_session,
    )
    event_bus = TurnEventBus()
    resolved_resources_factory = resources_factory or (
        lambda session_id, runtime_config, repo_root, database: build_session_resources(
            session_id,
            runtime_config,
            repo_root,
            database,
            memory_store=memory_store,
        )
    )
    session_registry = SessionRegistry(
        runtime_config=resolved_config,
        repo_root=resolved_repo_root,
        database=database,
        event_bus=event_bus,
        resources_factory=resolved_resources_factory,
    )
    static_dir = Path(__file__).resolve().parent / "static"
    admin_static_dir = Path(__file__).resolve().parent / "static_admin"
    extension_manager = ExtensionManager(repo_root=resolved_repo_root, runtime_config=resolved_config)
    extension_manager.discover()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup owns persisted state recovery; shutdown owns in-memory runtime cleanup.
        db_notice = migrate_legacy_http_database(database.db_path, resolved_repo_root)
        if db_notice:
            print(db_notice)
        database.initialize()
        database.mark_incomplete_turns_failed()
        yield
        session_registry.close_all()

    app = FastAPI(title="BabyClaw HTTP", lifespan=lifespan)
    app.state.runtime_config = resolved_config
    app.state.runtime_config_loader = Config.reload
    app.state.repo_root = resolved_repo_root
    app.state.database = database
    app.state.memory_store = memory_store
    app.state.extension_manager = extension_manager
    app.state.event_bus = event_bus
    app.state.session_registry = session_registry
    app.state.static_dir = static_dir
    app.state.admin_static_dir = admin_static_dir
    app.state.started_at = datetime.now()
    app.state.shutdown_requested = False
    app.include_router(router)
    app.include_router(admin_router)

    if resolved_config.server.serve_ui:
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/admin/static", StaticFiles(directory=admin_static_dir), name="admin-static")

    return app
