"""Tool system and built-in tool package for nano-claw."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
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


@dataclass
class ToolResult:
    """Standardized tool output."""

    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


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


def build_tool_registry(
    *,
    skill_manager,
    mcp_manager=None,
    subagent_manager=None,
    memory_store=None,
    include_subagent_tool: bool = True,
    tool_profile: ToolProfile = ToolProfile.BUILD,
    runtime_config=None,
) -> ToolRegistry:
    """Build the standard tool registry for a parent or child agent."""
    from src.config import config
    from src.tools.bash import BashTool
    from src.tools.plan_submit import SubmitPlanTool
    from src.tools.plan_write import WritePlanTool
    from src.tools.read import ReadTool
    from src.tools.readonly_shell import ReadOnlyShellTool
    from src.tools.skill import LoadSkillTool
    from src.tools.memory import MemoryReadTool, MemorySearchTool, MemoryWriteTool
    from src.tools.subagent import RunSubagentTool
    from src.tools.write import WriteTool

    runtime_config = runtime_config or config
    registry = ToolRegistry()
    registry.register(ReadTool())
    registry.register(LoadSkillTool(skill_manager))

    if tool_profile == ToolProfile.BUILD:
        registry.register(WriteTool())
        registry.register(BashTool())
        if memory_store is not None and runtime_config.memory.enabled:
            registry.register(MemoryReadTool(memory_store))
            registry.register(MemorySearchTool(memory_store))
            registry.register(MemoryWriteTool(memory_store))
        if mcp_manager is not None:
            mcp_manager.register_tools(registry)
        if include_subagent_tool and subagent_manager is not None and runtime_config.subagents.enabled:
            registry.register(RunSubagentTool(subagent_manager))
        return registry

    if tool_profile == ToolProfile.BUILD_SUBAGENT:
        registry.register(WriteTool())
        registry.register(BashTool())
        if mcp_manager is not None:
            mcp_manager.register_tools(registry)
        return registry

    registry.register(ReadOnlyShellTool())

    if tool_profile == ToolProfile.PLAN_MAIN:
        registry.register(WritePlanTool())
        registry.register(SubmitPlanTool())
        if (
            include_subagent_tool
            and subagent_manager is not None
            and runtime_config.subagents.enabled
            and runtime_config.plan.allow_subagents
        ):
            registry.register(RunSubagentTool(subagent_manager))
        return registry

    if tool_profile == ToolProfile.PLAN_SUBAGENT:
        return registry

    return registry


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
    "ToolRegistry",
    "ToolResult",
]
