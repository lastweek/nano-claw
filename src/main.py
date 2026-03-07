"""Main CLI entry point for nano-claw."""

from dataclasses import dataclass
import os
import random
import sys
import time
from pathlib import Path
from typing import Optional

# Add parent directory to path to allow imports when running directly
if __name__ == "__main__" and __file__:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from rich.console import Console, Group
from src.input_helper import InputHelper
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.live import Live

from src.context import Context
from src.llm import LLMClient
from src.memory import SessionMemoryStore
from src.metrics import LLMMetrics
from src.session_runtime import SessionRuntimeController
from src.tools import (
    ToolProfile,
    ToolRegistryReport,
    build_tool_registry,
    build_tool_registry_with_report,
)
from src.agent import Agent
from src.config import Config, config
from src.mcp import MCPManager
from src.skills import SkillManager
from src.commands import CommandRegistry
from src.commands import builtin
from src.statusline import build_prompt_toolbar
from src.turn_display import TurnProgressDisplay
from src.turn_controls import LiveTurnControls
from src.subagents import SubagentManager
from src.utils import env_truthy

REQUEST_TYPE_STREAMING = "streaming"
REQUEST_TYPE_NON_STREAMING = "non-streaming"
NANO_CLAW_WORDMARK = r""" ____    ____  __  __          _   _
|  _ \  / __||  \/  | __ _    | \ | |
| | | || (__ | |\/| |/ _` |   |  \| |
| |_| | \___|| |  | | (_| |   | |\  |
|____/  |___/ |_|  |_|\__,_|   |_| \_|
"""
# Backward-compatible alias used by older tests/docs.
NANO_CODER_WORDMARK = NANO_CLAW_WORDMARK
FIRE_CHARACTERS = " .,:;irsXA253hMHGS#9B&@"
FIRE_COLORS = (
    "#3a281f",
    "#5b3521",
    "#7d4823",
    "#9f5b25",
    "#c06f28",
    "#d98a3a",
    "#e5a95f",
    "#f1c98c",
)


def _build_banner_panel(llm_info_lines: list[Text], *, fire_text: Text | None = None) -> Panel:
    """Build the startup banner panel, optionally with one fire frame."""
    renderables = [
        Text(NANO_CLAW_WORDMARK.rstrip("\n"), style="bold cyan"),
    ]
    if fire_text is not None:
        renderables.append(fire_text)
    renderables.append(Text("Minimalism Terminal Code Agent", style="dim"))

    if llm_info_lines:
        renderables.append(Text(""))
        renderables.append(Text("\n").join(llm_info_lines))

    return Panel(
        Group(*renderables),
        border_style="cyan",
        padding=(1, 2),
    )


def _update_fire_heat(heat: list[list[float]], rng: random.Random) -> list[list[float]]:
    """Generate the next banner fire frame."""
    height = len(heat)
    width = len(heat[0]) if heat else 0
    next_heat = [[0.0 for _ in range(width)] for _ in range(height)]

    for x in range(width):
        if rng.random() < 0.72:
            next_heat[-1][x] = rng.uniform(0.48, 1.0)
        else:
            next_heat[-1][x] = heat[-1][x] * 0.35

    for y in range(height - 2, -1, -1):
        for x in range(width):
            source_x = min(width - 1, max(0, x + rng.choice((-1, 0, 1))))
            inherited = (next_heat[y + 1][x] + next_heat[y + 1][source_x]) / 2
            cooling = rng.uniform(0.05, 0.18)
            next_heat[y][x] = max(0.0, inherited - cooling)

    return next_heat


def _render_fire_frame(heat: list[list[float]], rng: random.Random) -> Text:
    """Render one original ASCII fire frame."""
    fire_text = Text()
    max_index = len(FIRE_CHARACTERS) - 1
    color_count = len(FIRE_COLORS)

    for row_index, row in enumerate(heat):
        for cell in row:
            intensity = max(0.0, min(1.0, cell))
            char_index = min(max_index, int(intensity * max_index))
            character = FIRE_CHARACTERS[char_index]
            if character == " " and intensity > 0.16 and rng.random() < 0.08:
                character = "."
            color_index = min(color_count - 1, int(intensity * (color_count - 1)))
            fire_text.append(character, style=FIRE_COLORS[color_index])
        if row_index != len(heat) - 1:
            fire_text.append("\n")

    return fire_text


def _animate_banner_fire(console: Console, llm_info_lines: list[Text]) -> None:
    """Show a brief original ASCII fire animation before the static banner."""
    width = max(len(line) for line in NANO_CLAW_WORDMARK.splitlines())
    height = 6
    rng = random.Random()
    heat = [[0.0 for _ in range(width)] for _ in range(height)]

    with Live(
        _build_banner_panel(llm_info_lines),
        console=console,
        transient=True,
        auto_refresh=False,
    ) as live:
        for _ in range(12):
            heat = _update_fire_heat(heat, rng)
            live.update(
                _build_banner_panel(
                    llm_info_lines,
                    fire_text=_render_fire_frame(heat, rng),
                ),
                refresh=True,
            )
            time.sleep(0.08)


def print_banner(console: Console, runtime_config=None) -> None:
    """Print welcome banner."""
    runtime_config = runtime_config or config

    # Build LLM info lines
    llm_info_lines = []
    if runtime_config.llm.base_url:
        llm_info_lines.append(Text(f"URL: {runtime_config.llm.base_url}", style="dim"))
    if runtime_config.llm.model:
        model_text = Text(f"Model: {runtime_config.llm.model}", style="dim")
        if runtime_config.llm.context_window:
            model_text.append(Text(f" (context: {runtime_config.llm.context_window:,} tokens)", style="dim"))
        llm_info_lines.append(model_text)
    if runtime_config.llm.provider:
        llm_info_lines.append(Text(f"Provider: {runtime_config.llm.provider}", style="dim"))

    if console.is_terminal:
        _animate_banner_fire(console, llm_info_lines)

    console.print(_build_banner_panel(llm_info_lines))
    console.print("[dim]Type 'exit' or 'quit' to leave[/dim]\n")


def _final_response_from_context(agent, fallback: str) -> str:
    """Prefer the finalized assistant response stored in context."""
    messages = agent.context.get_messages()
    if messages and messages[-1]["role"] == "assistant":
        return messages[-1]["content"]
    return fallback


def _calculate_aggregate_stream_tpot(metrics_list: list[LLMMetrics]) -> Optional[float]:
    """Aggregate TPOT across streamed requests using existing per-request semantics."""
    weighted_sum = 0.0
    weight_total = 0

    for metric in metrics_list:
        if metric.request_type != REQUEST_TYPE_STREAMING:
            continue
        if metric.tpot <= 0 or metric.token_count < 2:
            continue

        token_intervals = metric.token_count - 1
        weighted_sum += metric.tpot * token_intervals
        weight_total += token_intervals

    if weight_total == 0:
        return None
    return weighted_sum / weight_total


def display_metrics(console: Console, metrics_list: list[LLMMetrics], request_type: str) -> None:
    """Display aggregate request metrics after the final answer."""
    if not metrics_list:
        return

    total_prompt_tokens = sum(metric.prompt_tokens for metric in metrics_list)
    total_completion_tokens = sum(metric.completion_tokens for metric in metrics_list)
    total_duration = sum(metric.duration for metric in metrics_list)
    provider = metrics_list[0].provider if metrics_list else ""
    model = metrics_list[0].model if metrics_list else ""

    parts = [
        f"{total_prompt_tokens} prompt tokens",
        f"{total_completion_tokens} completion tokens",
        f"{total_duration:.2f}s",
    ]

    if request_type == REQUEST_TYPE_STREAMING:
        first_stream = next(
            (metric for metric in metrics_list if metric.request_type == REQUEST_TYPE_STREAMING),
            None,
        )
        if first_stream and first_stream.ttft > 0:
            parts.append(f"TTFT {first_stream.ttft:.2f}s")
        aggregate_stream_tpot = _calculate_aggregate_stream_tpot(metrics_list)
        if aggregate_stream_tpot is not None:
            parts.append(f"TPOT {aggregate_stream_tpot:.2f}s")

    if model and provider:
        parts.append(f"{model} ({provider})")

    console.print(Text(" • ".join(parts), style="dim"))


def run_agent_turn(
    console: Console,
    agent,
    user_input: str,
    *,
    enable_streaming: bool,
    skill_debug: bool,
) -> str:
    """Run one agent turn with a live chronological activity feed."""
    request_type = REQUEST_TYPE_STREAMING if enable_streaming else REQUEST_TYPE_NON_STREAMING
    runtime_config = getattr(agent, "runtime_config", None) or config
    display = TurnProgressDisplay(
        session_context=agent.context,
        skill_debug=skill_debug,
        request_type=request_type,
        live_activity_mode=runtime_config.ui.live_activity_mode,
        live_activity_details=runtime_config.ui.live_activity_details,
    )
    live_controls = LiveTurnControls(display)
    response = ""
    animate_live = console.is_terminal

    def handle_event(event) -> None:
        display.handle_event(event)

    try:
        with Live(
            display,
            console=console,
            transient=False,
            auto_refresh=animate_live,
            refresh_per_second=12 if animate_live else 4,
        ) as live:
            if animate_live:
                live_controls.start()
            if enable_streaming:
                for token in agent.run_stream(user_input, on_event=handle_event):
                    display.append_stream_chunk(token)
                response = _final_response_from_context(agent, display.final_response_text())
            else:
                response = agent.run(user_input, on_event=handle_event)
            live.update(display.render_persisted(), refresh=True)
    finally:
        live_controls.stop()

    if response:
        console.print()
        console.print(Markdown(response))

    return response


def execute_user_turn(
    console: Console,
    agent,
    user_input: str,
    *,
    enable_streaming: bool,
    skill_debug: bool,
) -> str:
    """Run one user-visible turn and print its final metrics summary."""
    console.print("\n[bold cyan]Agent[/bold cyan] >")
    response = run_agent_turn(
        console,
        agent,
        user_input,
        enable_streaming=enable_streaming,
        skill_debug=skill_debug,
    )
    request_type = REQUEST_TYPE_STREAMING if enable_streaming else REQUEST_TYPE_NON_STREAMING
    display_metrics(console, agent.request_metrics, request_type)
    return response


def display_streaming_response(console: Console, agent, user_input: str) -> str:
    """Backward-compatible streaming helper."""
    return run_agent_turn(
        console,
        agent,
        user_input,
        enable_streaming=True,
        skill_debug=False,
    )


@dataclass
class AppRuntime:
    """Assembled runtime dependencies for one CLI session."""

    runtime_config: Config
    skill_debug: bool
    enable_streaming: bool
    context: Context
    skill_manager: SkillManager
    mcp_manager: MCPManager | None
    subagent_manager: SubagentManager
    tools: object
    agent: Agent
    registry: CommandRegistry
    cmd_context: dict[str, object]
    input_helper: InputHelper


def build_console() -> Console:
    """Create the Rich console used by the CLI."""
    return Console(stderr=True)


def load_runtime_config(console: Console) -> Config:
    """Reload config after env bootstrap and print any load diagnostics."""
    runtime_config = Config.reload()
    for message in Config.get_load_messages():
        style = "dim"
        if message.startswith("Warning:"):
            style = "yellow"
        elif message.startswith("No config.yaml"):
            style = "yellow"
        console.print(f"[{style}]{message}[/{style}]")
    return runtime_config


def validate_provider_config(runtime_config: Config) -> str | None:
    """Return a configuration error for missing provider credentials, if any."""
    provider = runtime_config.llm.provider
    if provider in ("ollama", "local"):
        return None

    api_key_from_config = runtime_config.llm.api_key
    api_key_from_env = os.environ.get(f"{provider.upper()}_API_KEY") or os.environ.get("API_KEY")
    if api_key_from_config or api_key_from_env:
        return None

    return (
        f"API key not configured for provider '{provider}'.\n\n"
        "[dim]Set the API key in config.yaml:[/dim]\n"
        f"[dim]  llm:[/dim]\n[dim]    provider: {provider}[/dim]\n"
        "[dim]    api_key: your-key-here[/dim]"
    )


def discover_skills(
    console: Console,
    cwd: Path,
    *,
    runtime_config: Config,
    skill_debug: bool,
) -> SkillManager:
    """Discover local skills and print any warnings."""
    skill_manager = SkillManager(repo_root=cwd, runtime_config=runtime_config)
    skill_warnings = skill_manager.discover()

    for warning in skill_warnings:
        console.print(f"[yellow]Skill warning: {warning}[/yellow]")

    if skill_debug:
        discovered_skills = skill_manager.list_skills()
        console.print(f"[dim][SKILL] discovered {len(discovered_skills)} skill(s)[/dim]")
        for skill in discovered_skills:
            console.print(
                "[dim]"
                f"[SKILL] discovered {skill.name} "
                f"(source={skill.source}, catalog={'yes' if skill.catalog_visible else 'no'}, "
                f"eligible={'yes' if skill.eligible else 'no'})"
                "[/dim]"
            )

    return skill_manager


def build_mcp_manager(console: Console, runtime_config: Config) -> MCPManager | None:
    """Create the MCP manager when servers are configured."""
    if not runtime_config.mcp.servers:
        return None

    try:
        mcp_debug = env_truthy("MCP_DEBUG")
        if mcp_debug:
            console.print("[dim][MCP] Debug mode enabled[/dim]")

        enabled_servers = [server.name for server in runtime_config.mcp.servers if server.enabled]
        if enabled_servers and mcp_debug:
            console.print(f"[dim][MCP] Configured servers: {', '.join(enabled_servers)}[/dim]")

        servers_config = [
            {
                "name": server.name,
                "url": server.url,
                "enabled": server.enabled,
                "timeout": server.timeout,
            }
            for server in runtime_config.mcp.servers
        ]

        if mcp_debug:
            console.print("[dim][MCP] Creating MCP manager...[/dim]")

        mcp_manager = MCPManager(servers_config, debug=mcp_debug)
        if enabled_servers:
            if mcp_debug:
                console.print(
                    f"[dim][MCP] Successfully loaded {len(enabled_servers)} MCP server(s)[/dim]"
                )
            else:
                console.print(
                    f"[dim]Loaded {len(enabled_servers)} MCP server(s): {', '.join(enabled_servers)}[/dim]"
                )
        return mcp_manager
    except Exception as exc:
        console.print(f"[yellow]Warning: Failed to initialize MCP manager: {exc}[/yellow]")
        return None


def print_tool_debug_report(console: Console, report: ToolRegistryReport) -> None:
    """Print a stable CLI-only startup summary for tool registration."""
    console.print("[dim][TOOL] Debug mode enabled[/dim]")
    console.print(
        "[dim]"
        f"[TOOL] profile={report.tool_profile.value} "
        f"platform={report.platform} "
        f"registered={len(report.registered_tool_names)}"
        "[/dim]"
    )
    registered_tools = ", ".join(report.registered_tool_names) if report.registered_tool_names else "none"
    console.print(f"[dim][TOOL] registered tools: {registered_tools}[/dim]")
    for decision in report.group_decisions:
        console.print(f"[dim][TOOL] {decision.name}: {decision.status}[/dim]")
    for decision in report.tool_decisions:
        console.print(f"[dim][TOOL] {decision.name}: {decision.status}[/dim]")


def build_agent_runtime(
    console: Console,
    runtime_config: Config,
    cwd: Path,
    *,
    skill_debug: bool,
    tool_debug: bool,
) -> AppRuntime:
    """Assemble the runtime dependencies for the interactive CLI."""
    enable_streaming = runtime_config.ui.enable_streaming
    context = Context.create(cwd=str(cwd))
    skill_manager = discover_skills(
        console,
        cwd,
        runtime_config=runtime_config,
        skill_debug=skill_debug,
    )
    memory_store = SessionMemoryStore(repo_root=cwd, runtime_config=runtime_config)

    llm_client = LLMClient(runtime_config=runtime_config)
    mcp_manager = build_mcp_manager(console, runtime_config)
    subagent_manager = SubagentManager(runtime_config=runtime_config)
    tools, tool_report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        mcp_manager=mcp_manager,
        subagent_manager=subagent_manager,
        memory_store=memory_store,
        include_subagent_tool=runtime_config.subagents.enabled,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )
    if tool_debug:
        print_tool_debug_report(console, tool_report)
    agent = Agent(
        llm_client,
        tools,
        context,
        skill_manager=skill_manager,
        subagent_manager=subagent_manager,
        runtime_config=runtime_config,
        memory_store=memory_store,
    )

    registry = CommandRegistry()
    builtin.register_all(registry)

    cmd_context: dict[str, object] = {
        "agent": agent,
        "mcp_manager": mcp_manager,
        "tools": tools,
        "skill_manager": skill_manager,
        "session_context": context,
        "subagent_manager": subagent_manager,
        "memory_store": memory_store,
    }

    def apply_tool_profile(tool_profile: ToolProfile) -> None:
        """Rebuild the parent tool registry for the requested session tool profile."""
        rebuilt_tools = build_tool_registry(
            skill_manager=skill_manager,
            mcp_manager=mcp_manager,
            subagent_manager=subagent_manager,
            memory_store=memory_store,
            include_subagent_tool=True,
            tool_profile=tool_profile,
            runtime_config=runtime_config,
        )
        agent.set_tool_registry(rebuilt_tools)
        cmd_context["tools"] = rebuilt_tools

    session_runtime = SessionRuntimeController(
        session_context=context,
        agent=agent,
        skill_manager=skill_manager,
        mcp_manager=mcp_manager,
        subagent_manager=subagent_manager,
        apply_tool_profile=apply_tool_profile,
        logger=agent.logger,
        runtime_config=runtime_config,
    )
    cmd_context["session_runtime_controller"] = session_runtime

    command_descriptions = {
        cmd.name: cmd.short_desc or cmd.description
        for cmd in registry.list_commands()
    }
    command_names = registry.get_command_names()

    def build_idle_statusline():
        """Build the idle prompt statusline from session state."""
        return build_prompt_toolbar(
            context,
            view_mode=runtime_config.ui.live_activity_mode,
        )

    def toggle_plan_mode() -> None:
        """Toggle the top-level session mode between build and plan."""
        session_runtime.toggle_plan_mode()

    input_helper = InputHelper(
        command_names=command_names,
        command_descriptions=command_descriptions,
        skill_names=[skill.name for skill in skill_manager.list_skills()],
        bottom_toolbar_callback=build_idle_statusline,
        toggle_plan_mode_callback=toggle_plan_mode,
    )
    cmd_context["input_helper"] = input_helper
    cmd_context["prompt_input_callback"] = input_helper.get_input
    cmd_context["run_agent_turn_callback"] = lambda prompt: execute_user_turn(
        console,
        agent,
        prompt,
        enable_streaming=enable_streaming,
        skill_debug=skill_debug,
    )

    return AppRuntime(
        runtime_config=runtime_config,
        skill_debug=skill_debug,
        enable_streaming=enable_streaming,
        context=context,
        skill_manager=skill_manager,
        mcp_manager=mcp_manager,
        subagent_manager=subagent_manager,
        tools=tools,
        agent=agent,
        registry=registry,
        cmd_context=cmd_context,
        input_helper=input_helper,
    )


def run_repl(console: Console, runtime: AppRuntime) -> None:
    """Run the interactive CLI loop until exit."""
    while True:
        try:
            user_input = runtime.input_helper.get_input("\nYou > ")
            print()

            if not user_input.strip():
                continue

            if runtime.registry.execute(user_input, console, runtime.cmd_context):
                continue

            if user_input.lower() in ["exit", "quit"]:
                console.print("\n[yellow]Goodbye![/yellow]")
                break

            execute_user_turn(
                console,
                runtime.agent,
                user_input,
                enable_streaming=runtime.enable_streaming,
                skill_debug=runtime.skill_debug,
            )
        except KeyboardInterrupt:
            console.print("\n\n[yellow]Interrupted. Type 'exit' to quit.[/yellow]")
        except EOFError:
            console.print("\n\n[yellow]Goodbye![/yellow]")
            break
        except Exception as exc:
            console.print(f"\n[red]Error: {exc}[/red]")


def main() -> None:
    """Main entry point."""
    load_dotenv()
    argv = sys.argv[1:]
    skill_debug = env_truthy("SKILL_DEBUG")
    tool_debug = env_truthy("TOOL_DEBUG")
    console = build_console()
    runtime_config = load_runtime_config(console)

    if skill_debug:
        console.print("[dim][SKILL] Debug mode enabled[/dim]")

    provider_error = validate_provider_config(runtime_config)
    if provider_error:
        console.print(f"[red]Error: {provider_error}[/red]\n")
        sys.exit(1)

    if argv and argv[0] == "serve":
        from src.cli.serve import run_serve_command

        run_serve_command(argv[1:], runtime_config)
        return

    try:
        runtime = build_agent_runtime(
            console,
            runtime_config,
            Path.cwd(),
            skill_debug=skill_debug,
            tool_debug=tool_debug,
        )
    except Exception as exc:
        console.print(f"[red]Error initializing runtime: {exc}[/red]")
        sys.exit(1)

    print_banner(console, runtime_config)

    try:
        run_repl(console, runtime)
    finally:
        if hasattr(runtime.agent, "logger") and runtime.agent.logger:
            runtime.agent.logger.close()
        if runtime.mcp_manager:
            runtime.mcp_manager.close_all()


if __name__ == "__main__":
    main()
