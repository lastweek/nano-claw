"""Shared runtime capability rebuild and live-refresh helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.agent import Agent
from src.capabilities import CapabilityInventory, CapabilityRequestManager
from src.config import Config
from src.extensions import ExtensionManager
from src.mcp import MCPManager
from src.skills import SkillManager
from src.subagents import SubagentManager
from src.tools import ToolProfile, ToolRegistry, build_tool_registry
from src.tools.runtime_refresh import register_runtime_refresh_tool
from src.utils import env_truthy


@dataclass(frozen=True)
class RuntimeCapabilityBundle:
    """One fully rebuilt capability set for a live session."""

    runtime_config: Config
    extension_manager: ExtensionManager | None
    skill_manager: SkillManager
    mcp_manager: MCPManager | None
    subagent_manager: SubagentManager
    tool_registry: ToolRegistry
    capability_inventory: CapabilityInventory
    capability_request_manager: CapabilityRequestManager
    warnings: list[str]


@dataclass(frozen=True)
class RuntimeRefreshOutcome:
    """Structured diff returned after a live runtime refresh."""

    reason: str | None
    tool_profile: str
    added_tools: list[str]
    removed_tools: list[str]
    added_skills: list[str]
    removed_skills: list[str]
    pruned_skills: list[dict[str, str]]
    resolved_capability_request_ids: list[str]
    warnings: list[str]
    extensions: list[dict[str, str]]
    pending_capability_request_count: int

    def to_payload(self) -> dict:
        """Render a JSON-serializable diff."""
        return {
            "reason": self.reason,
            "tool_profile": self.tool_profile,
            "added_tools": self.added_tools,
            "removed_tools": self.removed_tools,
            "added_skills": self.added_skills,
            "removed_skills": self.removed_skills,
            "pruned_skills": self.pruned_skills,
            "resolved_capability_request_ids": self.resolved_capability_request_ids,
            "pending_capability_request_count": self.pending_capability_request_count,
            "warnings": self.warnings,
            "extensions": self.extensions,
        }


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


def build_runtime_capability_bundle(
    *,
    repo_root: Path,
    runtime_config: Config,
    tool_profile: ToolProfile,
    memory_store=None,
    capability_request_manager: CapabilityRequestManager | None = None,
    include_subagent_tool: bool = True,
    refresh_callback: Callable[[str | None], dict] | None = None,
) -> RuntimeCapabilityBundle:
    """Rebuild config-backed runtime capabilities for one session or CLI runtime."""
    extension_manager: ExtensionManager | None = None
    warnings: list[str] = []
    if runtime_config.extensions.enabled:
        extension_manager = ExtensionManager(repo_root=repo_root, runtime_config=runtime_config)
        warnings.extend(extension_manager.discover())

    skill_manager = SkillManager(
        repo_root=repo_root,
        runtime_config=runtime_config,
        extra_roots=extension_manager.get_skill_roots() if extension_manager is not None else None,
    )
    warnings.extend(skill_manager.discover())

    mcp_manager = _build_mcp_manager(runtime_config)
    subagent_manager = SubagentManager(runtime_config=runtime_config)
    request_manager = capability_request_manager or CapabilityRequestManager()
    capability_inventory = CapabilityInventory(
        repo_root=repo_root,
        runtime_config=runtime_config,
    )
    tool_registry = build_tool_registry(
        skill_manager=skill_manager,
        capability_inventory=capability_inventory,
        capability_request_manager=request_manager,
        extension_manager=extension_manager,
        mcp_manager=mcp_manager,
        subagent_manager=subagent_manager,
        memory_store=memory_store,
        include_subagent_tool=include_subagent_tool,
        tool_profile=tool_profile,
        runtime_config=runtime_config,
    )
    register_runtime_refresh_tool(
        tool_registry,
        tool_profile=tool_profile,
        refresh_callback=refresh_callback,
    )

    return RuntimeCapabilityBundle(
        runtime_config=runtime_config,
        extension_manager=extension_manager,
        skill_manager=skill_manager,
        mcp_manager=mcp_manager,
        subagent_manager=subagent_manager,
        tool_registry=tool_registry,
        capability_inventory=capability_inventory,
        capability_request_manager=request_manager,
        warnings=warnings,
    )


def refresh_live_runtime(
    *,
    repo_root: Path,
    agent: Agent,
    session_context,
    current_skill_manager: SkillManager | None,
    current_tool_registry: ToolRegistry | None,
    current_mcp_manager: MCPManager | None,
    capability_request_manager: CapabilityRequestManager | None = None,
    memory_store,
    tool_profile: ToolProfile,
    config_loader: Callable[[], Config],
    include_subagent_tool: bool = True,
    input_helper=None,
    refresh_callback: Callable[[str | None], dict] | None = None,
    reason: str | None = None,
) -> tuple[RuntimeCapabilityBundle, RuntimeRefreshOutcome]:
    """Reload config-backed capabilities and swap them into a live runtime."""
    previous_tool_names = sorted(current_tool_registry.list_tools()) if current_tool_registry is not None else []
    previous_skill_names = (
        sorted(skill.name for skill in current_skill_manager.list_skills())
        if current_skill_manager is not None
        else []
    )
    runtime_config = config_loader()
    bundle = build_runtime_capability_bundle(
        repo_root=repo_root,
        runtime_config=runtime_config,
        tool_profile=tool_profile,
        memory_store=memory_store,
        capability_request_manager=capability_request_manager,
        include_subagent_tool=include_subagent_tool,
        refresh_callback=refresh_callback,
    )

    pruned_skills: list[dict[str, str]] = []
    for skill_name in list(session_context.get_active_skills()):
        skill = bundle.skill_manager.get_skill(skill_name)
        if skill is None:
            session_context.deactivate_skill(skill_name)
            pruned_skills.append({"name": skill_name, "reason": "missing"})
        elif not skill.eligible:
            session_context.deactivate_skill(skill_name)
            pruned_skills.append(
                {"name": skill_name, "reason": skill.eligibility_reason or "ineligible"}
            )

    agent.skill_manager = bundle.skill_manager
    agent.subagent_manager = bundle.subagent_manager
    agent.runtime_config = bundle.runtime_config
    agent.max_iterations = bundle.runtime_config.agent.max_iterations
    if getattr(agent, "tool_runtime", None) is not None:
        agent.tool_runtime.subagent_manager = bundle.subagent_manager
    if getattr(agent, "context_compaction", None) is not None:
        agent.context_compaction.skill_manager = bundle.skill_manager
    if hasattr(agent, "set_tool_registry"):
        agent.set_tool_registry(bundle.tool_registry)
    else:
        agent.tools = bundle.tool_registry

    if memory_store is not None and hasattr(memory_store, "runtime_config"):
        memory_store.runtime_config = bundle.runtime_config

    if input_helper is not None:
        input_helper.update_skills([skill.name for skill in bundle.skill_manager.list_skills()])

    if current_mcp_manager is not None and current_mcp_manager is not bundle.mcp_manager:
        current_mcp_manager.close_all()

    current_tool_names = sorted(bundle.tool_registry.list_tools())
    current_skill_names = sorted(skill.name for skill in bundle.skill_manager.list_skills())
    resolved_capability_request_ids = bundle.capability_request_manager.auto_resolve(
        tool_registry=bundle.tool_registry,
        skill_manager=bundle.skill_manager,
        extension_manager=bundle.extension_manager,
    )
    outcome = RuntimeRefreshOutcome(
        reason=reason,
        tool_profile=tool_profile.value,
        added_tools=sorted(set(current_tool_names) - set(previous_tool_names)),
        removed_tools=sorted(set(previous_tool_names) - set(current_tool_names)),
        added_skills=sorted(set(current_skill_names) - set(previous_skill_names)),
        removed_skills=sorted(set(previous_skill_names) - set(current_skill_names)),
        pruned_skills=pruned_skills,
        resolved_capability_request_ids=resolved_capability_request_ids,
        pending_capability_request_count=bundle.capability_request_manager.pending_count(),
        warnings=list(bundle.warnings),
        extensions=[
            {
                "name": extension.name,
                "version": extension.version,
                "source": extension.install_scope,
            }
            for extension in (
                bundle.extension_manager.list_extensions()
                if bundle.extension_manager is not None
                else []
            )
        ],
    )
    return bundle, outcome
