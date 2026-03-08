"""Session-scoped runtime resources for one long-lived HTTP session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.agent import Agent
from src.capabilities import CapabilityInventory, CapabilityRequestManager
from src.config import Config
from src.context import Context
from src.llm import LLMClient
from src.logger import SessionLogger
from src.memory import SessionMemory
from src.mcp import MCPManager
from src.runtime_refresh import build_runtime_capability_bundle
from src.skills import SkillManager
from src.database.session_database import SessionDatabase, SessionSnapshot, deserialize_session_summary
from src.subagents import SubagentManager
from src.tools import ToolProfile, ToolRegistry


@dataclass
class SessionResources:
    """Loaded per-session objects reused across turns."""

    agent: Agent
    context: Context
    logger: SessionLogger
    mcp_manager: MCPManager | None
    skill_manager: SkillManager | None = None
    extension_manager: object | None = None
    tool_registry: ToolRegistry | None = None
    subagent_manager: SubagentManager | None = None
    memory_store: SessionMemory | None = None
    capability_inventory: CapabilityInventory | None = None
    capability_request_manager: CapabilityRequestManager | None = None

    def close(self, *, status: str = "completed") -> None:
        cleanup_error: Exception | None = None
        try:
            self.logger.close(status=status)
        except Exception as exc:
            cleanup_error = exc
        try:
            if self.mcp_manager:
                self.mcp_manager.close_all()
        except Exception as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if cleanup_error is not None:
            raise cleanup_error


SessionResourcesFactory = Callable[[str, Config, Path, SessionDatabase], SessionResources]


def _build_context_from_snapshot(repo_root: Path, session_snapshot: SessionSnapshot) -> Context:
    context = Context(
        cwd=repo_root,
        session_id=session_snapshot.session.id,
    )
    context.messages = [
        {"role": message.role, "content": message.content}
        for message in session_snapshot.messages
    ]
    context.summary = deserialize_session_summary(session_snapshot.summary_json)
    context.active_skills = []
    context.session_mode = "build"
    return context
def build_session_resources(
    session_id: str,
    runtime_config: Config,
    repo_root: Path,
    database: SessionDatabase,
    memory_store: SessionMemory | None = None,
) -> SessionResources:
    """Build one reusable resource bundle for a long-lived session runtime."""
    session_snapshot = database.get_session_snapshot(session_id)
    if session_snapshot is None:
        raise KeyError(f"Unknown session: {session_id}")

    # Building these separately keeps SessionRuntime focused on lifecycle and turn execution.
    context = _build_context_from_snapshot(repo_root, session_snapshot)
    memory_store = memory_store or SessionMemory(
        repo_root=repo_root,
        runtime_config=runtime_config,
        session_lookup=database.get_session,
    )
    logger: SessionLogger | None = None

    try:
        capability_bundle = build_runtime_capability_bundle(
            repo_root=repo_root,
            runtime_config=runtime_config,
            tool_profile=ToolProfile.BUILD,
            memory_store=memory_store,
            include_subagent_tool=runtime_config.subagents.enabled,
        )
        skill_manager = capability_bundle.skill_manager
        mcp_manager = capability_bundle.mcp_manager
        subagent_manager = capability_bundle.subagent_manager
        tool_registry = capability_bundle.tool_registry
        llm_client = LLMClient(runtime_config=runtime_config)
        logger = SessionLogger(
            session_id,
            runtime_config=runtime_config,
            session_title=session_snapshot.session.title,
            session_created_at=session_snapshot.session.created_at,
        )
        agent = Agent(
            llm_client,
            tool_registry,
            context,
            skill_manager=skill_manager,
            logger=logger,
            subagent_manager=subagent_manager,
            runtime_config=runtime_config,
            memory_store=memory_store,
        )
    except Exception:
        try:
            if logger is not None:
                logger.close(status="error")
        except Exception:
            pass
        try:
            if mcp_manager is not None:
                mcp_manager.close_all()
        except Exception:
            pass
        raise

    return SessionResources(
        agent=agent,
        context=context,
        logger=logger,
        mcp_manager=mcp_manager,
        skill_manager=skill_manager,
        extension_manager=capability_bundle.extension_manager,
        tool_registry=tool_registry,
        subagent_manager=subagent_manager,
        memory_store=memory_store,
        capability_inventory=capability_bundle.capability_inventory,
        capability_request_manager=capability_bundle.capability_request_manager,
    )
