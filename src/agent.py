"""Agent orchestration for nano-claw."""

from dataclasses import dataclass
import json
from typing import Callable, Optional, List, Dict, Any, Literal, Tuple
from src.agent_compaction import run_auto_compaction_if_needed
from src.agent_turn_prep import PendingSkillEvent, build_conversation_messages, prepare_turn_input
from src.activity_preview import (
    build_assistant_preview,
    build_tool_signature,
)
from src.plan_mode import build_build_execution_contract, build_plan_prompt
from src.message_types import ChatMessage, ToolCallPayload, ToolResultPayload
from src.tools import (
    ROLE_SYSTEM, ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL,
    REQUEST_KIND_AGENT_TURN,
    REQUEST_KIND_PLAN_TURN,
)
from src.logger import SessionLogger
from src.metrics import LLMMetrics
from src.config import Config
from src.context_compaction import ContextCompactionManager, ContextCompactionPolicy
from src.tool_runtime import AgentToolRuntime, ToolBatchOutcome
from src.turn_activity import TurnActivityCallback, TurnActivityEvent


@dataclass(frozen=True)
class _ModelIterationResult:
    """Outcome of one model iteration inside the shared turn loop."""

    outcome: Literal["final_answer", "tool_calls"]
    assistant_text: str
    requested_tool_calls: List[ToolCallPayload]


@dataclass
class _AgentTurnState:
    """Mutable state tracked across one top-level agent turn."""

    turn_id: int
    normalized_user_message: str
    conversation_messages: List[ChatMessage]
    tools_used: List[str]
    skills_used: List[str]
    tool_call_count: int = 0
    is_finished: bool = False


class Agent:
    """Main agent orchestration using ReAct loop."""

    def __init__(
        self,
        llm_client,
        tools,
        context,
        skill_manager=None,
        logger: Optional[SessionLogger] = None,
        request_kind: str = REQUEST_KIND_AGENT_TURN,
        subagent_manager=None,
        runtime_config: Optional[Config] = None,
    ):
        """Initialize the agent.

        Args:
            llm_client: LLMClient instance
            tools: ToolRegistry instance
            context: Context instance
            skill_manager: Optional SkillManager instance
        """
        self.llm = llm_client
        self.tools = tools
        self.context = context
        self.skill_manager = skill_manager
        self.request_kind = request_kind
        self.subagent_manager = subagent_manager
        self.runtime_config = runtime_config or Config.load()
        current_config = self.runtime_config
        self.max_iterations = current_config.agent.max_iterations
        self.logger = logger or SessionLogger(context.session_id, runtime_config=current_config)
        self.logger.start_session(
            cwd=self.context.cwd,
            provider=getattr(self.llm, "provider", "unknown"),
            model=getattr(self.llm, "model", "unknown"),
            base_url=getattr(self.llm, "base_url", None),
            streaming_enabled=current_config.ui.enable_streaming,
        )

        # Share logger with LLM client for request/response logging
        if hasattr(self.llm, 'logger'):
            self.llm.logger = self.logger

        # Accumulate metrics across LLM requests
        self.request_metrics: List[LLMMetrics] = []
        self.context_compaction = ContextCompactionManager(
            self.llm,
            self.context,
            self.skill_manager,
            ContextCompactionPolicy(
                auto_compact=current_config.context.auto_compact,
                auto_compact_threshold=current_config.context.auto_compact_threshold,
                target_usage_after_compaction=current_config.context.target_usage_after_compaction,
                min_recent_turns=current_config.context.min_recent_turns,
            ),
        )
        self._cached_tool_schemas = None
        self._skill_event_callback = None
        self.tool_runtime = AgentToolRuntime(
            parent_agent=self,
            context=self.context,
            logger=self.logger,
            subagent_manager=self.subagent_manager,
            get_tool=lambda name: self.tools.get(name),
            build_tool_result_message=self._build_tool_result_message,
            parse_tool_arguments_for_logging=self._parse_tool_arguments_for_logging,
            emit_turn_event=self._emit_turn_event,
            emit_skill_event=self._emit_skill_event,
        )
        self._refresh_system_prompt_base()

    def set_skill_event_callback(self, callback: Optional[Callable]) -> None:
        """Set an optional callback for skill debug events."""
        self._skill_event_callback = callback

    def set_tool_registry(self, tools) -> None:
        """Replace the active tool registry and invalidate cached prompt/schema state."""
        self.tools = tools
        self._cached_tool_schemas = None
        self._refresh_system_prompt_base()

    def _build_system_message(self) -> Dict:
        """Build the system message for the current turn."""
        return {"role": ROLE_SYSTEM, "content": self._build_system_prompt()}

    def _build_system_prompt(self) -> str:
        """Build the full system prompt for the current turn."""
        sections = [self._cached_system_prompt_base]
        mode_section = self._build_mode_prompt_section()
        if mode_section:
            sections.append(mode_section)

        skill_catalog = self._build_skill_catalog_section()
        if skill_catalog:
            sections.append(skill_catalog)

        return "\n\n".join(section for section in sections if section)

    def _build_mode_prompt_section(self) -> str:
        """Build any session-mode-specific prompt section for this turn."""
        if self.context.get_session_mode() == "plan":
            return build_plan_prompt(
                self.context,
                can_write_plan=self.tools.get("write_plan") is not None,
                can_submit_plan=self.tools.get("submit_plan") is not None,
            )

        return build_build_execution_contract(self.context)

    def _build_skill_catalog_section(self) -> str:
        """Build the compact catalog of discovered skills for this session."""
        if not self.skill_manager:
            return ""

        skills = self.skill_manager.list_catalog_skills()
        if not skills:
            return ""

        lines = ["Available skills in this session:"]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.short_description}")

        lines.extend([
            "",
            "Load a skill only when needed by calling load_skill.",
            "Users may also explicitly preload a skill by writing $skill-name.",
        ])
        return "\n".join(lines)

    def _emit_skill_event(self, turn_id: int, event: str, **details) -> None:
        """Log and optionally surface a skill-related debug event."""
        self.logger.log_skill_event(turn_id, event, **details)
        if self._skill_event_callback is not None:
            self._skill_event_callback(event, details)

    def _emit_turn_event(
        self,
        callback: Optional[TurnActivityCallback],
        kind: str,
        *,
        iteration: Optional[int] = None,
        **details: Any,
    ) -> None:
        """Emit a user-safe activity event for the CLI."""
        if callback is None:
            return
        callback(
            TurnActivityEvent(
                kind=kind,
                iteration=iteration,
                details=details,
            )
        )

    def _replay_pending_skill_events(
        self,
        turn_id: int,
        pending_skill_events: List[PendingSkillEvent],
        on_event: Optional[TurnActivityCallback],
        skills_used: List[str],
    ) -> None:
        """Write deferred skill events after a turn id exists."""
        for pending_event in pending_skill_events:
            event_name = pending_event.name
            details = pending_event.details
            self._emit_skill_event(turn_id, event_name, **details)

            if event_name == "preload":
                skill_name = details.get("skill_name")
                if skill_name and skill_name not in skills_used:
                    skills_used.append(skill_name)
                self._emit_turn_event(on_event, "skill_preload", **details)
            elif event_name == "normalized_user_message":
                self._emit_turn_event(on_event, "skill_normalized", **details)

    def _build_metrics_event_details(self, metrics: Any) -> Dict[str, Any]:
        """Normalize metric fields for activity events."""
        return {
            "duration_s": getattr(metrics, "duration", 0.0),
            "prompt_tokens": getattr(metrics, "prompt_tokens", 0),
            "completion_tokens": getattr(metrics, "completion_tokens", 0),
            "total_tokens": getattr(metrics, "total_tokens", 0),
            "cached_tokens": getattr(metrics, "cached_tokens", 0),
        }

    def _build_llm_log_context(self, turn_id: int, iteration: int, *, stream: bool) -> Dict[str, Any]:
        """Build the shared LLM logging context for one request."""
        return {
            "turn_id": turn_id,
            "iteration": iteration,
            "stream": stream,
            "request_kind": self._current_request_kind(),
        }

    def _current_request_kind(self) -> str:
        """Return the request kind for the current turn."""
        if self.request_kind != REQUEST_KIND_AGENT_TURN:
            return self.request_kind
        if self.context.get_session_mode() == "plan":
            return REQUEST_KIND_PLAN_TURN
        return self.request_kind

    def _refresh_system_prompt_base(self) -> None:
        """Rebuild the static prompt base from the current tool registry."""
        tool_descriptions = "\n".join(
            f"- {tool.name}: {tool.description}"
            for tool in self.tools._tools.values()
        )

        self._cached_system_prompt_base = f"""You are a helpful coding assistant with access to tools.

Working directory: {self.context.cwd}

Available tools:
{tool_descriptions}

When asked to do something that requires tools, use them. Always explain what you're doing before using a tool.
Think step by step. If you make a mistake, try to recover.

Be concise and helpful."""
        if self.tools.get("run_subagent") is not None:
            self._cached_system_prompt_base += (
                "\n\nWhen a task can be split into independent repo subtasks, you may delegate it with run_subagent."
            )

    def _emit_llm_call_started(
        self,
        on_event: Optional[TurnActivityCallback],
        *,
        iteration: int,
        stream: bool,
        messages: List[Dict[str, Any]],
    ) -> None:
        """Emit the live event for an outgoing LLM request."""
        self._emit_turn_event(
            on_event,
            "llm_call_started",
            iteration=iteration,
            stream=stream,
            message_count=len(messages),
            tool_schema_count=len(self._cached_tool_schemas),
        )

    def _emit_llm_call_finished(
        self,
        on_event: Optional[TurnActivityCallback],
        *,
        iteration: int,
        stream: bool,
        metrics: Any,
        tool_calls: List[Dict[str, Any]],
        assistant_text: str,
    ) -> None:
        """Emit the live event for a completed LLM request."""
        requested_tool_signatures = self._build_requested_tool_signatures(tool_calls)
        assistant_preview, assistant_body = build_assistant_preview(
            assistant_text,
            requested_tool_signatures,
        )
        self._emit_turn_event(
            on_event,
            "llm_call_finished",
            iteration=iteration,
            stream=stream,
            **self._build_metrics_event_details(metrics),
            has_tool_calls=bool(tool_calls),
            tool_call_count=len(tool_calls),
            result_kind="tool_calls" if tool_calls else "final_answer",
            assistant_preview=assistant_preview,
            assistant_body=assistant_body,
            requested_tool_signatures=requested_tool_signatures,
        )

    def _build_requested_tool_signatures(self, tool_calls: List[Dict[str, Any]]) -> List[str]:
        """Render compact signatures for a model-issued tool-call batch."""
        signatures: List[str] = []
        for tool_call in tool_calls:
            arguments = self._parse_tool_arguments_for_logging(tool_call.get("arguments", "{}"))
            signatures.append(build_tool_signature(tool_call.get("name"), arguments))
        return signatures

    def _store_recent_prompt_metrics(self, metrics: Any) -> None:
        """Persist the most recent prompt usage for compaction decisions."""
        prompt_tokens = getattr(metrics, "prompt_tokens", None)
        if isinstance(prompt_tokens, int) and prompt_tokens > 0:
            self.context.last_prompt_tokens = prompt_tokens
            cached_tokens = getattr(metrics, "cached_tokens", None)
            self.context.last_prompt_cached_tokens = (
                cached_tokens if isinstance(cached_tokens, int) else None
            )
        else:
            self.context.last_prompt_tokens = None
            self.context.last_prompt_cached_tokens = None
        self.context.last_context_window = self.runtime_config.llm.context_window

    def _create_turn_state(
        self,
        user_message: str,
        on_event: Optional[TurnActivityCallback] = None,
    ) -> _AgentTurnState:
        """Create the mutable state object for one top-level agent turn.

        Args:
            user_message: The user's input message
            on_event: Optional callback for events

        Returns:
            Mutable turn state shared by the main agent loop.
        """
        prepared_turn_input = prepare_turn_input(
            user_message,
            context=self.context,
            skill_manager=self.skill_manager,
        )
        turn_id = self.logger.start_turn(
            raw_user_input=user_message,
            normalized_user_input=prepared_turn_input.normalized_user_message,
        )
        tools_used: List[str] = []
        skills_used: List[str] = []
        self._replay_pending_skill_events(
            turn_id,
            prepared_turn_input.pending_skill_events,
            on_event,
            skills_used,
        )

        # Cache tool schemas for hot-path optimization (only once)
        if self._cached_tool_schemas is None:
            self._cached_tool_schemas = self.tools.get_tool_schemas()

        self._run_auto_compaction_if_needed(turn_id, on_event)
        conversation_messages = build_conversation_messages(
            system_message=self._build_system_message(),
            summary_message=self.context.get_summary_message(),
            history_messages=self.context.get_messages(),
            skill_manager=self.skill_manager,
            preload_skill_names=prepared_turn_input.preload_skill_names,
            normalized_user_message=prepared_turn_input.normalized_user_message,
            role_user=ROLE_USER,
        )

        return _AgentTurnState(
            turn_id=turn_id,
            normalized_user_message=prepared_turn_input.normalized_user_message,
            conversation_messages=conversation_messages,
            tools_used=tools_used,
            skills_used=skills_used,
        )

    def _prepare_user_message_for_turn(
        self,
        user_message: str,
    ) -> Tuple[str, List[str], List[Tuple[str, Dict[str, Any]]]]:
        """Normalize the turn input and gather skill preloads without building messages."""
        prepared_turn_input = prepare_turn_input(
            user_message,
            context=self.context,
            skill_manager=self.skill_manager,
        )
        return (
            prepared_turn_input.normalized_user_message,
            prepared_turn_input.preload_skill_names,
            [
                (pending_event.name, pending_event.details)
                for pending_event in prepared_turn_input.pending_skill_events
            ],
        )

    def _build_conversation_messages(
        self,
        normalized_user_message: str,
        preload_skill_names: List[str],
    ) -> List[Dict]:
        """Build the message list for the next LLM call."""
        return build_conversation_messages(
            system_message=self._build_system_message(),
            summary_message=self.context.get_summary_message(),
            history_messages=self.context.get_messages(),
            skill_manager=self.skill_manager,
            preload_skill_names=preload_skill_names,
            normalized_user_message=normalized_user_message,
            role_user=ROLE_USER,
        )

    def _run_auto_compaction_if_needed(
        self,
        turn_id: int,
        on_event: Optional[TurnActivityCallback],
    ) -> None:
        """Compact older turns before the first model call when policy requires it."""
        run_auto_compaction_if_needed(self, turn_id, on_event)

    def _build_assistant_history_message(self, response: Dict[str, Any]) -> ChatMessage:
        """Convert an assistant response into a replayable conversation message."""
        message = {
            "role": response["role"],
            "content": response.get("content", ""),
        }

        tool_calls = response.get("tool_calls", [])
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": tool_call["id"],
                    "type": "function",
                    "function": {
                        "name": tool_call["name"],
                        "arguments": tool_call["arguments"],
                    },
                }
                for tool_call in tool_calls
            ]

        return message

    def _build_tool_result_message(self, tool_id: str, result: ToolResultPayload | Dict[str, Any]) -> ChatMessage:
        """Build the replayable tool result message appended to the conversation."""
        return {
            "role": ROLE_TOOL,
            "tool_call_id": tool_id,
            "content": json.dumps(result),
        }

    def _parse_tool_arguments_for_logging(self, raw_arguments: str) -> Dict[str, Any]:
        """Parse tool arguments for logging and UI callbacks without aborting the turn."""
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            return {
                "raw_arguments": raw_arguments,
                "parse_error": str(exc),
            }
        if isinstance(arguments, dict):
            return arguments
        return {"value": arguments}

    def _append_completed_turn_to_context(self, user_message: str, final_response: str) -> None:
        """Save and log final agent response.

        Args:
            user_message: Original user message
            final_response: Final response text
        """
        self.context.add_message(ROLE_USER, user_message)
        self.context.add_message(ROLE_ASSISTANT, final_response)
        # Note: Response is already logged by llm_client.log_llm_response()
        # No need to log again here

    def _emit_turn_completed_event(
        self,
        on_event: Optional[TurnActivityCallback],
        *,
        status: str,
        tool_call_count: int,
        tools_used: List[str],
        skills_used: List[str],
    ) -> None:
        """Emit the shared turn-completed event payload."""
        self._emit_turn_event(
            on_event,
            "turn_completed",
            status=status,
            llm_call_count=len(self.request_metrics),
            tool_call_count=tool_call_count,
            tools_used=tools_used,
            skills_used=skills_used,
        )

    def _finish_successful_turn(
        self,
        *,
        turn_id: int,
        user_message: str,
        final_response: str,
        tool_call_count: int,
        tools_used: List[str],
        skills_used: List[str],
        on_event: Optional[TurnActivityCallback],
    ) -> str:
        """Finalize a successful turn and return the response text."""
        self._append_completed_turn_to_context(user_message, final_response)
        self.logger.finish_turn(
            turn_id, final_response, self.request_metrics, status="completed"
        )
        self._emit_turn_completed_event(
            on_event,
            status="completed",
            tool_call_count=tool_call_count,
            tools_used=tools_used,
            skills_used=skills_used,
        )
        return final_response

    def _finish_turn_after_max_iterations(
        self,
        normalized_user_message: str,
        turn_id: int,
        tool_call_count: int,
        tools_used: List[str],
        skills_used: List[str],
        on_event: Optional[TurnActivityCallback] = None,
    ) -> str:
        """Handle max iterations error case.

        Args:
            normalized_user_message: The normalized user message
            turn_id: Turn ID for logging
            tool_call_count: Number of tool calls made
            tools_used: List of tools used
            skills_used: List of skills used
            on_event: Optional callback for events

        Returns:
            Error response message
        """
        error_response = "I reached the maximum number of iterations. Please try a simpler request."
        self.context.add_message(ROLE_USER, normalized_user_message)
        self.context.add_message(ROLE_ASSISTANT, error_response)
        self.logger.log_error(
            turn_id=turn_id,
            phase="agent.max_iterations",
            message=error_response,
            details={},
        )
        self.logger.finish_turn(
            turn_id,
            error_response,
            self.request_metrics,
            status="error",
            error={"phase": "agent.max_iterations", "message": error_response},
        )
        self._emit_turn_event(
            on_event,
            "turn_error",
            phase="agent.max_iterations",
            message=error_response,
        )
        self._emit_turn_completed_event(
            on_event,
            status="error",
            tool_call_count=tool_call_count,
            tools_used=tools_used,
            skills_used=skills_used,
        )
        return error_response

    def _finish_turn_after_exception(
        self,
        exc: Exception,
        turn_id: int,
        tool_call_count: int,
        tools_used: List[str],
        skills_used: List[str],
        turn_finished: bool,
        on_event: Optional[TurnActivityCallback] = None,
        phase: str = "agent.run",
    ) -> None:
        """Handle exception in agent loop.

        Args:
            exc: The exception that occurred
            turn_id: Turn ID for logging
            tool_call_count: Number of tool calls made
            tools_used: List of tools used
            skills_used: List of skills used
            turn_finished: Whether the turn was already finished
            on_event: Optional callback for events
            phase: Phase where exception occurred
        """
        self.logger.log_error(
            turn_id=turn_id,
            phase=phase,
            message=str(exc),
            details={"exception_type": type(exc).__name__},
        )
        if not turn_finished:
            self.logger.finish_turn(
                turn_id,
                "",
                self.request_metrics,
                status="error",
                error={"phase": phase, "message": str(exc)},
            )
        self._emit_turn_event(
            on_event,
            "turn_error",
            phase=phase,
            message=str(exc),
        )
        self._emit_turn_completed_event(
            on_event,
            status="error",
            tool_call_count=tool_call_count,
            tools_used=tools_used,
            skills_used=skills_used,
        )

    def _run_non_stream_model_iteration(
        self,
        *,
        turn_id: int,
        iteration: int,
        conversation_messages: List[Dict[str, Any]],
        on_event: Optional[TurnActivityCallback],
    ) -> _ModelIterationResult:
        """Run one non-streaming LLM iteration and return its outcome."""
        self._emit_llm_call_started(
            on_event,
            iteration=iteration,
            stream=False,
            messages=conversation_messages,
        )
        response, metrics = self.llm.chat(
            conversation_messages,
            tools=self._cached_tool_schemas,
            log_context=self._build_llm_log_context(turn_id, iteration, stream=False),
        )
        metrics.iteration = iteration
        self.request_metrics.append(metrics)
        self._store_recent_prompt_metrics(metrics)
        conversation_messages.append(self._build_assistant_history_message(response))

        tool_calls = response.get("tool_calls", [])
        self._emit_llm_call_finished(
            on_event,
            iteration=iteration,
            stream=False,
            metrics=metrics,
            tool_calls=tool_calls,
            assistant_text=response.get("content", ""),
        )
        if not tool_calls:
            return _ModelIterationResult(
                outcome="final_answer",
                assistant_text=response.get("content", ""),
                requested_tool_calls=[],
            )
        return _ModelIterationResult(
            outcome="tool_calls",
            assistant_text="",
            requested_tool_calls=tool_calls,
        )

    def _run_stream_model_iteration(
        self,
        *,
        turn_id: int,
        iteration: int,
        conversation_messages: List[Dict[str, Any]],
        on_event: Optional[TurnActivityCallback],
    ):
        """Run one streaming LLM iteration and yield response chunks as they arrive."""
        self._emit_llm_call_started(
            on_event,
            iteration=iteration,
            stream=True,
            messages=conversation_messages,
        )
        streamed_text_chunks: List[str] = []
        assistant_role = "assistant"

        for chunk in self.llm.chat_stream(
            conversation_messages,
            tools=self._cached_tool_schemas,
            log_context=self._build_llm_log_context(turn_id, iteration, stream=True),
        ):
            if "role" in chunk:
                assistant_role = chunk["role"]
            if "delta" in chunk:
                streamed_text_chunks.append(chunk["delta"])
                yield chunk["delta"]

        stream_metrics = self.llm.get_stream_metrics()
        if stream_metrics:
            stream_metrics.iteration = iteration
            self.request_metrics.append(stream_metrics)
            self._store_recent_prompt_metrics(stream_metrics)

        tool_calls = self.llm.get_stream_tool_calls()
        metrics_for_event = stream_metrics or LLMMetrics()
        self._emit_llm_call_finished(
            on_event,
            iteration=iteration,
            stream=True,
            metrics=metrics_for_event,
            tool_calls=tool_calls,
            assistant_text="".join(streamed_text_chunks),
        )

        if not tool_calls:
            return _ModelIterationResult(
                outcome="final_answer",
                assistant_text="".join(streamed_text_chunks),
                requested_tool_calls=[],
            )

        conversation_messages.append(
            self._build_assistant_history_message({
                "role": assistant_role,
                "content": "".join(streamed_text_chunks),
                "tool_calls": tool_calls,
            })
        )
        return _ModelIterationResult(
            outcome="tool_calls",
            assistant_text="",
            requested_tool_calls=tool_calls,
        )

    def _run_agent_turn(
        self,
        user_message: str,
        *,
        stream: bool,
        on_tool_call: Optional[Callable],
        on_event: Optional[TurnActivityCallback],
    ):
        """Run the shared agent turn loop, yielding chunks only in streaming mode."""
        self.request_metrics.clear()
        turn_state = self._create_turn_state(user_message, on_event)

        try:
            for iteration in range(self.max_iterations):
                if stream:
                    model_iteration = yield from self._run_stream_model_iteration(
                        turn_id=turn_state.turn_id,
                        iteration=iteration,
                        conversation_messages=turn_state.conversation_messages,
                        on_event=on_event,
                    )
                else:
                    model_iteration = self._run_non_stream_model_iteration(
                        turn_id=turn_state.turn_id,
                        iteration=iteration,
                        conversation_messages=turn_state.conversation_messages,
                        on_event=on_event,
                    )

                if model_iteration.outcome == "final_answer":
                    final_response = self._finish_successful_turn(
                        turn_id=turn_state.turn_id,
                        user_message=turn_state.normalized_user_message,
                        final_response=model_iteration.assistant_text,
                        tool_call_count=turn_state.tool_call_count,
                        tools_used=turn_state.tools_used,
                        skills_used=turn_state.skills_used,
                        on_event=on_event,
                    )
                    turn_state.is_finished = True
                    return final_response

                tool_processing_result = self.tool_runtime.process_tool_calls(
                    model_iteration.requested_tool_calls,
                    messages=turn_state.conversation_messages,
                    turn_id=turn_state.turn_id,
                    iteration=iteration,
                    on_tool_call=on_tool_call,
                    on_event=on_event,
                    tools_used=turn_state.tools_used,
                    skills_used=turn_state.skills_used,
                )
                turn_state.tool_call_count += tool_processing_result.processed_count
                if tool_processing_result.terminal_response is not None:
                    final_response = self._finish_successful_turn(
                        turn_id=turn_state.turn_id,
                        user_message=turn_state.normalized_user_message,
                        final_response=tool_processing_result.terminal_response,
                        tool_call_count=turn_state.tool_call_count,
                        tools_used=turn_state.tools_used,
                        skills_used=turn_state.skills_used,
                        on_event=on_event,
                    )
                    turn_state.is_finished = True
                    return final_response

            if stream:
                error_response = "I reached the maximum number of iterations. Please try a simpler request."
                for char in error_response:
                    yield char
                return self._finish_turn_after_max_iterations(
                    turn_state.normalized_user_message,
                    turn_state.turn_id,
                    turn_state.tool_call_count,
                    turn_state.tools_used,
                    turn_state.skills_used,
                    on_event,
                )
            return self._finish_turn_after_max_iterations(
                turn_state.normalized_user_message,
                turn_state.turn_id,
                turn_state.tool_call_count,
                turn_state.tools_used,
                turn_state.skills_used,
                on_event,
            )
        except Exception as exc:
            self._finish_turn_after_exception(
                exc,
                turn_state.turn_id,
                turn_state.tool_call_count,
                turn_state.tools_used,
                turn_state.skills_used,
                turn_state.is_finished,
                on_event,
                "agent.run_stream" if stream else "agent.run",
            )
            raise

    def _drain_turn_runner(self, turn_runner) -> str:
        """Exhaust a shared turn runner and return its final response string."""
        while True:
            try:
                next(turn_runner)
            except StopIteration as stop:
                return stop.value

    def _process_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        messages: List[Dict[str, Any]],
        turn_id: int,
        iteration: int,
        on_tool_call: Optional[Callable] = None,
        on_event: Optional[TurnActivityCallback] = None,
        tools_used: Optional[List[str]] = None,
        skills_used: Optional[List[str]] = None,
    ) -> ToolBatchOutcome:
        """Compatibility wrapper around the extracted tool runtime."""
        return self.tool_runtime.process_tool_calls(
            tool_calls,
            messages=messages,
            turn_id=turn_id,
            iteration=iteration,
            on_tool_call=on_tool_call,
            on_event=on_event,
            tools_used=tools_used,
            skills_used=skills_used,
        )

    def run(
        self,
        user_message: str,
        on_tool_call: Optional[Callable] = None,
        on_event: Optional[TurnActivityCallback] = None,
    ) -> str:
        """Main agent loop - process user message and return response.

        Args:
            user_message: The user's input message
            on_tool_call: Optional callback called with tool_name, args before execution
            on_event: Optional callback for user-safe live activity events

        Returns:
            The agent's final response as a string
        """
        return self._drain_turn_runner(
            self._run_agent_turn(
                user_message,
                stream=False,
                on_tool_call=on_tool_call,
                on_event=on_event,
            )
        )

    def run_stream(
        self,
        user_message: str,
        on_tool_call: Optional[Callable] = None,
        on_event: Optional[TurnActivityCallback] = None,
    ):
        """Stream agent responses token-by-token.

        Args:
            user_message: The user's input message
            on_tool_call: Optional callback called with tool_name, args before execution
            on_event: Optional callback for user-safe live activity events

        Yields:
            Tokens/chunks of the response as they arrive
        """
        yield from self._run_agent_turn(
            user_message,
            stream=True,
            on_tool_call=on_tool_call,
            on_event=on_event,
        )
