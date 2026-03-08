"""Tool system and built-in tool package for nano-claw."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from src.context import Context


# Constants for message structure
ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"

# Constants for request kind tracking
REQUEST_KIND_AGENT_TURN = "agent_turn"
REQUEST_KIND_CONTEXT_COMPACTION = "context_compaction"
REQUEST_KIND_PLAN_TURN = "plan_turn"
REQUEST_KIND_SUBAGENT_TURN = "subagent_turn"


class ToolProfile(str, Enum):
    """Tool profile names with type safety.

    Different profiles control which tools are available to agents in different modes:
    - BUILD: Full tool access for normal agent operation
    - PLAN_MAIN: Planning mode tools for main agent
    - PLAN_SUBAGENT: Planning mode tools for subagents (restricted)
    - BUILD_SUBAGENT: Full tools for subagents
    """

    BUILD = "build"
    PLAN_MAIN = "plan_main"
    PLAN_SUBAGENT = "plan_subagent"
    BUILD_SUBAGENT = "build_subagent"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ToolRegistrationDecision:
    """Stable debug status for one optional tool or tool group."""

    name: str
    status: str


@dataclass(frozen=True)
class ToolRegistryReport:
    """Structured report describing how optional tool registration resolved."""

    tool_profile: ToolProfile
    platform: str
    registered_tool_names: tuple[str, ...]
    group_decisions: tuple[ToolRegistrationDecision, ...]
    tool_decisions: tuple[ToolRegistrationDecision, ...]


@dataclass
class ToolResult:
    """Standardized tool output."""

    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class Tool:
    """Base class for all agent tools."""

    name: str = ""
    description: str = ""
    parameters: dict = field(default_factory=dict)

    def execute(self, context: "Context", **kwargs) -> ToolResult:
        """Execute the tool with given arguments."""
        raise NotImplementedError(f"{self.__class__.__name__}.execute() not implemented")

    def _require_param(self, kwargs: dict, name: str) -> Any:
        """Get a required parameter or raise ValueError."""
        value = kwargs.get(name)
        if not value:
            raise ValueError(f"{name} is required")
        return value

    def _resolve_path(self, context: "Context", file_path: str) -> Path:
        """Resolve a file path relative to the current working directory."""
        return context.cwd / file_path

    def to_schema(self) -> Dict:
        """Convert tool to OpenAI function calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Register and manage available tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_tool_schemas(self) -> List[Dict]:
        """Get all tools as OpenAI function schemas."""
        return [tool.to_schema() for tool in self._tools.values()]

    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())


@dataclass
class _ToolBuildPlan:
    """Concrete optional-registration plan shared by the builder and debug reporting."""

    tool_profile: ToolProfile
    platform: str
    register_memory: bool = False
    register_mcp: bool = False
    register_subagent: bool = False
    register_finder: bool = False
    register_calendar: bool = False
    register_notes: bool = False
    register_reminders: bool = False
    register_messages: bool = False
    register_fetch_url: bool = False
    register_read_webpage: bool = False
    register_extract_page_links: bool = False
    register_find_capabilities: bool = False
    register_request_capability: bool = False
    register_extension_tools: list[str] = field(default_factory=list)
    group_decisions: list[ToolRegistrationDecision] = field(default_factory=list)
    tool_decisions: list[ToolRegistrationDecision] = field(default_factory=list)


def _registered(name: str) -> ToolRegistrationDecision:
    """Return a stable registered decision entry."""
    return ToolRegistrationDecision(name=name, status="registered")


def _skipped(name: str, reason: str) -> ToolRegistrationDecision:
    """Return a stable skipped decision entry."""
    return ToolRegistrationDecision(name=name, status=f"skipped: {reason}")


def _profile_reason(tool_profile: ToolProfile, *required_profiles: str) -> str:
    """Format a stable profile-gating reason."""
    return f"tool profile is {tool_profile.value}, requires {' or '.join(required_profiles)}"


def _build_optional_tool_plan(
    *,
    capability_inventory,
    capability_request_manager,
    extension_manager,
    mcp_manager,
    subagent_manager,
    memory_store,
    include_subagent_tool: bool,
    tool_profile: ToolProfile,
    runtime_config,
) -> _ToolBuildPlan:
    """Compute optional tool registration decisions once for both runtime and debug output."""
    platform = sys.platform
    plan = _ToolBuildPlan(tool_profile=tool_profile, platform=platform)

    if tool_profile != ToolProfile.BUILD:
        plan.group_decisions.append(_skipped("memory", _profile_reason(tool_profile, "build")))
    elif not runtime_config.memory.enabled:
        plan.group_decisions.append(_skipped("memory", "memory is disabled"))
    elif memory_store is None:
        plan.group_decisions.append(_skipped("memory", "memory store unavailable"))
    else:
        plan.register_memory = True
        plan.group_decisions.append(_registered("memory"))

    macos_flags = {
        "finder_action": runtime_config.macos_tools.enable_finder,
        "calendar_action": runtime_config.macos_tools.enable_calendar,
        "notes_action": runtime_config.macos_tools.enable_notes,
        "reminders_action": runtime_config.macos_tools.enable_reminders,
        "messages_action": runtime_config.macos_tools.enable_messages,
    }
    if tool_profile != ToolProfile.BUILD:
        reason = _profile_reason(tool_profile, "build")
        plan.group_decisions.append(_skipped("macos_tools", reason))
        for tool_name in macos_flags:
            plan.tool_decisions.append(_skipped(tool_name, reason))
    elif not runtime_config.macos_tools.enabled:
        reason = "macos_tools.enabled is false"
        plan.group_decisions.append(_skipped("macos_tools", reason))
        for tool_name in macos_flags:
            plan.tool_decisions.append(_skipped(tool_name, reason))
    elif platform != "darwin":
        reason = f"platform is {platform}, requires darwin"
        plan.group_decisions.append(_skipped("macos_tools", reason))
        for tool_name in macos_flags:
            plan.tool_decisions.append(_skipped(tool_name, reason))
    else:
        if any(macos_flags.values()):
            plan.group_decisions.append(_registered("macos_tools"))
        else:
            plan.group_decisions.append(_skipped("macos_tools", "no macos app tools enabled"))

        if macos_flags["finder_action"]:
            plan.register_finder = True
            plan.tool_decisions.append(_registered("finder_action"))
        else:
            plan.tool_decisions.append(_skipped("finder_action", "macos_tools.enable_finder is false"))

        if macos_flags["calendar_action"]:
            plan.register_calendar = True
            plan.tool_decisions.append(_registered("calendar_action"))
        else:
            plan.tool_decisions.append(_skipped("calendar_action", "macos_tools.enable_calendar is false"))

        if macos_flags["notes_action"]:
            plan.register_notes = True
            plan.tool_decisions.append(_registered("notes_action"))
        else:
            plan.tool_decisions.append(_skipped("notes_action", "macos_tools.enable_notes is false"))

        if macos_flags["reminders_action"]:
            plan.register_reminders = True
            plan.tool_decisions.append(_registered("reminders_action"))
        else:
            plan.tool_decisions.append(
                _skipped("reminders_action", "macos_tools.enable_reminders is false")
            )

        if macos_flags["messages_action"]:
            plan.register_messages = True
            plan.tool_decisions.append(_registered("messages_action"))
        else:
            plan.tool_decisions.append(
                _skipped("messages_action", "macos_tools.enable_messages is false")
            )

    web_flags = {
        "fetch_url": runtime_config.web_tools.enable_fetch_url,
        "read_webpage": runtime_config.web_tools.enable_read_webpage,
        "extract_page_links": runtime_config.web_tools.enable_extract_page_links,
    }
    if tool_profile not in (ToolProfile.BUILD, ToolProfile.BUILD_SUBAGENT, ToolProfile.PLAN_MAIN):
        reason = _profile_reason(tool_profile, "build", "build_subagent", "plan_main")
        plan.group_decisions.append(_skipped("web_tools", reason))
        for tool_name in web_flags:
            plan.tool_decisions.append(_skipped(tool_name, reason))
    elif not runtime_config.web_tools.enabled:
        reason = "web_tools.enabled is false"
        plan.group_decisions.append(_skipped("web_tools", reason))
        for tool_name in web_flags:
            plan.tool_decisions.append(_skipped(tool_name, reason))
    else:
        if any(web_flags.values()):
            plan.group_decisions.append(_registered("web_tools"))
        else:
            plan.group_decisions.append(_skipped("web_tools", "no web tools enabled"))

        if web_flags["fetch_url"]:
            plan.register_fetch_url = True
            plan.tool_decisions.append(_registered("fetch_url"))
        else:
            plan.tool_decisions.append(_skipped("fetch_url", "web_tools.enable_fetch_url is false"))

        if web_flags["read_webpage"]:
            plan.register_read_webpage = True
            plan.tool_decisions.append(_registered("read_webpage"))
        else:
            plan.tool_decisions.append(_skipped("read_webpage", "web_tools.enable_read_webpage is false"))

        if web_flags["extract_page_links"]:
            plan.register_extract_page_links = True
            plan.tool_decisions.append(_registered("extract_page_links"))
        else:
            plan.tool_decisions.append(
                _skipped("extract_page_links", "web_tools.enable_extract_page_links is false")
            )

    if tool_profile not in (ToolProfile.BUILD, ToolProfile.PLAN_MAIN):
        reason = _profile_reason(tool_profile, "build", "plan_main")
        plan.group_decisions.append(_skipped("capability_tools", reason))
        plan.tool_decisions.append(_skipped("find_capabilities", reason))
        plan.tool_decisions.append(_skipped("request_capability", reason))
    elif capability_inventory is None:
        reason = "capability inventory unavailable"
        plan.group_decisions.append(_skipped("capability_tools", reason))
        plan.tool_decisions.append(_skipped("find_capabilities", reason))
        plan.tool_decisions.append(_skipped("request_capability", reason))
    elif capability_request_manager is None:
        reason = "capability request manager unavailable"
        plan.group_decisions.append(_skipped("capability_tools", reason))
        plan.tool_decisions.append(_skipped("find_capabilities", reason))
        plan.tool_decisions.append(_skipped("request_capability", reason))
    else:
        plan.register_find_capabilities = True
        plan.register_request_capability = True
        plan.group_decisions.append(_registered("capability_tools"))
        plan.tool_decisions.append(_registered("find_capabilities"))
        plan.tool_decisions.append(_registered("request_capability"))

    enabled_mcp_servers = [server for server in runtime_config.mcp.servers if server.enabled]
    if tool_profile not in (ToolProfile.BUILD, ToolProfile.BUILD_SUBAGENT):
        plan.group_decisions.append(
            _skipped("mcp", _profile_reason(tool_profile, "build", "build_subagent"))
        )
    elif not enabled_mcp_servers:
        plan.group_decisions.append(_skipped("mcp", "no MCP servers enabled"))
    elif mcp_manager is None:
        plan.group_decisions.append(_skipped("mcp", "mcp manager unavailable"))
    else:
        plan.register_mcp = True
        plan.group_decisions.append(_registered("mcp"))

    if tool_profile not in (ToolProfile.BUILD, ToolProfile.BUILD_SUBAGENT, ToolProfile.PLAN_MAIN):
        reason = _profile_reason(tool_profile, "build", "build_subagent", "plan_main")
        plan.group_decisions.append(_skipped("extensions", reason))
    elif not runtime_config.extensions.enabled:
        plan.group_decisions.append(_skipped("extensions", "extensions.enabled is false"))
    elif extension_manager is None:
        plan.group_decisions.append(_skipped("extensions", "extension manager unavailable"))
    else:
        extension_tool_names = [
            tool_spec.name
            for _extension, tool_spec in extension_manager.get_tool_specs()
        ]
        if extension_tool_names:
            plan.group_decisions.append(_registered("extensions"))
            for tool_name in extension_tool_names:
                plan.register_extension_tools.append(tool_name)
                plan.tool_decisions.append(_registered(tool_name))
        else:
            plan.group_decisions.append(_skipped("extensions", "no extension tools discovered"))

    if tool_profile not in (ToolProfile.BUILD, ToolProfile.PLAN_MAIN):
        plan.group_decisions.append(
            _skipped("subagents", _profile_reason(tool_profile, "build", "plan_main"))
        )
    elif not runtime_config.subagents.enabled:
        plan.group_decisions.append(_skipped("subagents", "subagents are disabled"))
    elif not include_subagent_tool:
        plan.group_decisions.append(_skipped("subagents", "include_subagent_tool is false"))
    elif tool_profile == ToolProfile.PLAN_MAIN and not runtime_config.plan.allow_subagents:
        plan.group_decisions.append(_skipped("subagents", "plan.allow_subagents is false"))
    elif subagent_manager is None:
        plan.group_decisions.append(_skipped("subagents", "subagent manager unavailable"))
    else:
        plan.register_subagent = True
        plan.group_decisions.append(_registered("subagents"))

    return plan


def build_tool_registry(
    *,
    skill_manager,
    capability_inventory=None,
    capability_request_manager=None,
    extension_manager=None,
    mcp_manager=None,
    subagent_manager=None,
    memory_store=None,
    include_subagent_tool: bool = True,
    tool_profile: ToolProfile = ToolProfile.BUILD,
    runtime_config=None,
) -> ToolRegistry:
    """Build the standard tool registry for a parent or child agent."""
    registry, _report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        capability_inventory=capability_inventory,
        capability_request_manager=capability_request_manager,
        extension_manager=extension_manager,
        mcp_manager=mcp_manager,
        subagent_manager=subagent_manager,
        memory_store=memory_store,
        include_subagent_tool=include_subagent_tool,
        tool_profile=tool_profile,
        runtime_config=runtime_config,
    )
    return registry


def build_tool_registry_with_report(
    *,
    skill_manager,
    capability_inventory=None,
    capability_request_manager=None,
    extension_manager=None,
    mcp_manager=None,
    subagent_manager=None,
    memory_store=None,
    include_subagent_tool: bool = True,
    tool_profile: ToolProfile = ToolProfile.BUILD,
    runtime_config=None,
) -> tuple[ToolRegistry, ToolRegistryReport]:
    """Build the standard tool registry plus a structured optional-tool debug report."""
    from src.config import config
    from src.tools.bash import BashTool
    from src.tools.capability import FindCapabilitiesTool, RequestCapabilityTool
    from src.tools.extension import ExtensionTool
    from src.tools.plan_submit import SubmitPlanTool
    from src.tools.plan_write import WritePlanTool
    from src.tools.read import ReadTool
    from src.tools.readonly_shell import ReadOnlyShellTool
    from src.tools.skill import LoadSkillTool
    from src.tools.memory import MemoryReadTool, MemorySearchTool, MemoryWriteTool
    from src.tools.macos import (
        CalendarActionTool,
        FinderActionTool,
        MacOSHelper,
        MessagesActionTool,
        NotesActionTool,
        RemindersActionTool,
    )
    from src.tools.subagent import RunSubagentTool
    from src.tools.web import ExtractPageLinksTool, FetchURLTool, ReadWebpageTool, WebClient
    from src.tools.write import WriteTool

    runtime_config = runtime_config or config
    registry = ToolRegistry()
    registry.register(ReadTool())
    registry.register(LoadSkillTool(skill_manager))
    plan = _build_optional_tool_plan(
        capability_inventory=capability_inventory,
        capability_request_manager=capability_request_manager,
        extension_manager=extension_manager,
        mcp_manager=mcp_manager,
        subagent_manager=subagent_manager,
        memory_store=memory_store,
        include_subagent_tool=include_subagent_tool,
        tool_profile=tool_profile,
        runtime_config=runtime_config,
    )

    if tool_profile == ToolProfile.BUILD:
        registry.register(WriteTool())
        registry.register(BashTool())
        if plan.register_find_capabilities:
            registry.register(FindCapabilitiesTool(capability_inventory))
        if plan.register_request_capability:
            registry.register(RequestCapabilityTool(capability_request_manager))
        if plan.register_memory:
            registry.register(MemoryReadTool(memory_store))
            registry.register(MemorySearchTool(memory_store))
            registry.register(MemoryWriteTool(memory_store))
        if (
            plan.register_finder
            or plan.register_calendar
            or plan.register_notes
            or plan.register_reminders
            or plan.register_messages
        ):
            helper = MacOSHelper(timeout_seconds=runtime_config.macos_tools.timeout_seconds)
            if plan.register_finder:
                registry.register(FinderActionTool(helper))
            if plan.register_calendar:
                registry.register(CalendarActionTool(helper))
            if plan.register_notes:
                registry.register(NotesActionTool(helper))
            if plan.register_reminders:
                registry.register(RemindersActionTool(helper))
            if plan.register_messages:
                registry.register(MessagesActionTool(helper))
        if (
            plan.register_fetch_url
            or plan.register_read_webpage
            or plan.register_extract_page_links
        ):
            web_client = WebClient(
                timeout_seconds=runtime_config.web_tools.timeout_seconds,
                max_response_bytes=runtime_config.web_tools.max_response_bytes,
                max_content_chars=runtime_config.web_tools.max_content_chars,
                allow_private_networks=runtime_config.web_tools.allow_private_networks,
            )
            if plan.register_fetch_url:
                registry.register(FetchURLTool(web_client))
            if plan.register_read_webpage:
                registry.register(ReadWebpageTool(web_client))
            if plan.register_extract_page_links:
                registry.register(ExtractPageLinksTool(web_client))
        if plan.register_mcp:
            mcp_manager.register_tools(registry)
        if plan.register_extension_tools:
            for extension, tool_spec in extension_manager.get_tool_specs():
                if tool_spec.name not in plan.register_extension_tools:
                    continue
                registry.register(
                    ExtensionTool(
                        extension,
                        tool_spec,
                        timeout_seconds=runtime_config.extensions.runner_timeout_seconds,
                    )
                )
        if plan.register_subagent:
            registry.register(RunSubagentTool(subagent_manager))
    elif tool_profile == ToolProfile.BUILD_SUBAGENT:
        registry.register(WriteTool())
        registry.register(BashTool())
        if (
            plan.register_fetch_url
            or plan.register_read_webpage
            or plan.register_extract_page_links
        ):
            web_client = WebClient(
                timeout_seconds=runtime_config.web_tools.timeout_seconds,
                max_response_bytes=runtime_config.web_tools.max_response_bytes,
                max_content_chars=runtime_config.web_tools.max_content_chars,
                allow_private_networks=runtime_config.web_tools.allow_private_networks,
            )
            if plan.register_fetch_url:
                registry.register(FetchURLTool(web_client))
            if plan.register_read_webpage:
                registry.register(ReadWebpageTool(web_client))
            if plan.register_extract_page_links:
                registry.register(ExtractPageLinksTool(web_client))
        if plan.register_mcp:
            mcp_manager.register_tools(registry)
        if plan.register_extension_tools:
            for extension, tool_spec in extension_manager.get_tool_specs():
                if tool_spec.name not in plan.register_extension_tools:
                    continue
                registry.register(
                    ExtensionTool(
                        extension,
                        tool_spec,
                        timeout_seconds=runtime_config.extensions.runner_timeout_seconds,
                    )
                )
    else:
        registry.register(ReadOnlyShellTool())

    if tool_profile == ToolProfile.PLAN_MAIN:
        if plan.register_find_capabilities:
            registry.register(FindCapabilitiesTool(capability_inventory))
        if plan.register_request_capability:
            registry.register(RequestCapabilityTool(capability_request_manager))
        if (
            plan.register_fetch_url
            or plan.register_read_webpage
            or plan.register_extract_page_links
        ):
            web_client = WebClient(
                timeout_seconds=runtime_config.web_tools.timeout_seconds,
                max_response_bytes=runtime_config.web_tools.max_response_bytes,
                max_content_chars=runtime_config.web_tools.max_content_chars,
                allow_private_networks=runtime_config.web_tools.allow_private_networks,
            )
            if plan.register_fetch_url:
                registry.register(FetchURLTool(web_client))
            if plan.register_read_webpage:
                registry.register(ReadWebpageTool(web_client))
            if plan.register_extract_page_links:
                registry.register(ExtractPageLinksTool(web_client))
        registry.register(WritePlanTool())
        registry.register(SubmitPlanTool())
        if plan.register_extension_tools:
            for extension, tool_spec in extension_manager.get_tool_specs():
                if tool_spec.name not in plan.register_extension_tools:
                    continue
                registry.register(
                    ExtensionTool(
                        extension,
                        tool_spec,
                        timeout_seconds=runtime_config.extensions.runner_timeout_seconds,
                    )
                )
        if plan.register_subagent:
                registry.register(RunSubagentTool(subagent_manager))

    if capability_inventory is not None:
        capability_inventory.bind_runtime(
            tool_registry=registry,
            skill_manager=skill_manager,
            extension_manager=extension_manager,
        )

    report = ToolRegistryReport(
        tool_profile=tool_profile,
        platform=plan.platform,
        registered_tool_names=tuple(sorted(registry.list_tools())),
        group_decisions=tuple(plan.group_decisions),
        tool_decisions=tuple(plan.tool_decisions),
    )
    return registry, report


def clone_tool_registry(
    source: ToolRegistry,
    *,
    include_subagent_tool: bool = True,
    exclude_tools: set[str] | None = None,
) -> ToolRegistry:
    """Clone a registry by reusing tool instances from an existing registry."""
    registry = ToolRegistry()
    excluded = set(exclude_tools or set())
    for tool in source._tools.values():
        if not include_subagent_tool and tool.name == "run_subagent":
            continue
        if tool.name in excluded:
            continue
        registry.register(tool)
    return registry


__all__ = [
    "build_tool_registry",
    "build_tool_registry_with_report",
    "clone_tool_registry",
    "REQUEST_KIND_AGENT_TURN",
    "REQUEST_KIND_CONTEXT_COMPACTION",
    "REQUEST_KIND_PLAN_TURN",
    "REQUEST_KIND_SUBAGENT_TURN",
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_USER",
    "Tool",
    "ToolProfile",
    "ToolRegistrationDecision",
    "ToolRegistry",
    "ToolRegistryReport",
    "ToolResult",
]
