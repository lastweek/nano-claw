"""Built-in slash command registration."""

from src.commands.capability_cmds import register_capability_commands
from src.commands.context_cmds import register_context_commands
from src.commands.extension_cmds import register_extension_commands
from src.commands.help_cmds import register_help_commands
from src.commands.memory_cmds import register_memory_commands
from src.commands.mcp_cmds import register_mcp_commands
from src.commands.plan_cmds import register_plan_commands
from src.commands.runtime_cmds import register_runtime_commands
from src.commands.skill_cmds import register_skill_commands
from src.commands.subagent_cmds import register_subagent_commands


def register_all(registry) -> None:
    """Register all built-in slash commands."""
    register_help_commands(registry)
    register_capability_commands(registry)
    register_context_commands(registry)
    register_runtime_commands(registry)
    register_memory_commands(registry)
    register_subagent_commands(registry)
    register_plan_commands(registry)
    register_mcp_commands(registry)
    register_skill_commands(registry)
    register_extension_commands(registry)
