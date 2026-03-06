"""Session-scoped runtime resources for one long-lived HTTP session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.agent import Agent
from src.config import Config
from src.context import Context
from src.llm import LLMClient
from src.logger import SessionLogger
from src.mcp import MCPManager
from src.skills import SkillManager
from src.store.repository import AppStore, SessionSnapshot, deserialize_summary
from src.subagents import SubagentManager
from src.tools import ToolProfile, ToolRegistry, build_tool_registry
from src.utils import env_truthy


@dataclass
class SessionResources:
    """Loaded per-session objects reused across turns."""

    agent: Agent
    context: Context
    logger: SessionLogger
    mcp_manager: MCPManager | None
    skill_manager: SkillManager | None = None
    tool_registry: ToolRegistry | None = None
    subagent_manager: SubagentManager | None = None

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


SessionResourcesFactory = Callable[[str, Config, Path, AppStore], SessionResources]


def _build_context_from_snapshot(repo_root: Path, session_snapshot: SessionSnapshot) -> Context:
    context = Context(
        cwd=repo_root,
        session_id=session_snapshot.session.id,
    )
    context.messages = [
        {"role": message.role, "content": message.content}
        for message in session_snapshot.messages
    ]
    context.summary = deserialize_summary(session_snapshot.summary_json)
    context.active_skills = []
    context.session_mode = "build"
    return context


def _build_mcp_manager(runtime_config: Config) -> MCPManager | None:
    if not runtime_config.mcp.servers:
        return None
    servers_config = [
        {
            "name": server.name,
            "url": server.url,
            "enabled": server.enabled,
            "timeout": server.timeout,
        }
        for server in runtime_config.mcp.servers
    ]
    return MCPManager(servers_config, debug=env_truthy("MCP_DEBUG"))


def build_session_resources(
    session_id: str,
    runtime_config: Config,
    repo_root: Path,
    store: AppStore,
) -> SessionResources:
    """Build one reusable resource bundle for a long-lived session runtime."""
    session_snapshot = store.get_session_snapshot(session_id)
    if session_snapshot is None:
        raise KeyError(f"Unknown session: {session_id}")

    # Building these separately keeps SessionRuntime focused on lifecycle and turn execution.
    context = _build_context_from_snapshot(repo_root, session_snapshot)
    skill_manager = SkillManager(repo_root=repo_root)
    skill_manager.discover()
    mcp_manager = _build_mcp_manager(runtime_config)
    logger: SessionLogger | None = None

    try:
        subagent_manager = SubagentManager(runtime_config=runtime_config)
        tool_registry = build_tool_registry(
            skill_manager=skill_manager,
            mcp_manager=mcp_manager,
            subagent_manager=subagent_manager,
            include_subagent_tool=runtime_config.subagents.enabled,
            tool_profile=ToolProfile.BUILD,
            runtime_config=runtime_config,
        )
        llm_client = LLMClient(runtime_config=runtime_config)
        logger = SessionLogger(session_id, runtime_config=runtime_config)
        agent = Agent(
            llm_client,
            tool_registry,
            context,
            skill_manager=skill_manager,
            logger=logger,
            subagent_manager=subagent_manager,
            runtime_config=runtime_config,
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
        tool_registry=tool_registry,
        subagent_manager=subagent_manager,
    )
