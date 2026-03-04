"""Tool execution runtime extracted from the agent turn loop."""

from __future__ import annotations

from dataclasses import dataclass
import json
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

from src.activity_preview import build_tool_result_preview
from src.message_types import ChatMessage, ToolCallPayload, ToolResultPayload


@dataclass(frozen=True)
class ToolBatchOutcome:
    """Outcome of processing one model-issued tool-call batch."""

    processed_count: int
    terminal_response: str | None = None


@dataclass(frozen=True)
class _ResolvedSubagentToolCall:
    """One resolved run_subagent tool call in original batch order."""

    tool_call_id: str
    request: Any | None = None
    error_payload: ToolResultPayload | None = None


class AgentToolRuntime:
    """Execute normal and control-plane tools for one agent."""

    def __init__(
        self,
        *,
        parent_agent,
        context,
        logger,
        subagent_manager,
        get_tool: Callable[[str], Any],
        build_tool_result_message: Callable[[str, ToolResultPayload], ChatMessage],
        parse_tool_arguments_for_logging: Callable[[str], Dict[str, Any]],
        emit_turn_event: Callable[..., None],
        emit_skill_event: Callable[..., None],
    ) -> None:
        self.parent_agent = parent_agent
        self.context = context
        self.logger = logger
        self.subagent_manager = subagent_manager
        self.get_tool = get_tool
        self.build_tool_result_message = build_tool_result_message
        self.parse_tool_arguments_for_logging = parse_tool_arguments_for_logging
        self.emit_turn_event = emit_turn_event
        self.emit_skill_event = emit_skill_event

    def execute_standard_tool_call(
        self,
        tool_call: ToolCallPayload,
        parsed_args: Dict[str, Any],
    ) -> tuple[ChatMessage, ToolResultPayload]:
        """Execute one ordinary tool call and build the tool-result message."""
        tool_name = tool_call["name"]
        tool_id = tool_call["id"]

        try:
            tool = self.get_tool(tool_name)
            if not tool:
                result = {"error": f"Unknown tool: {tool_name}"}
            else:
                result_obj = tool.execute(self.context, **parsed_args)
                if result_obj.success:
                    result = {"output": str(result_obj.data)}
                else:
                    result = {"error": result_obj.error or "Tool execution failed"}
        except json.JSONDecodeError:
            result = {"error": f"Invalid JSON in tool arguments: {tool_call['arguments']}"}
        except Exception as exc:
            result = {"error": f"Error executing tool: {exc}"}

        return self.build_tool_result_message(tool_id, result), result

    def execute_submit_plan_tool(
        self,
        parsed_args: Dict[str, Any],
    ) -> ToolResultPayload:
        """Execute submit_plan and return the raw control-plane payload."""
        tool = self.get_tool("submit_plan")
        if tool is None:
            return {"error": "submit_plan is not available in the current tool profile"}

        result_obj = tool.execute(self.context, **parsed_args)
        if not result_obj.success:
            return {"error": result_obj.error or "submit_plan failed"}

        if isinstance(result_obj.data, dict):
            return result_obj.data
        return {
            "summary": str(result_obj.data or ""),
            "report": str(result_obj.data or ""),
        }

    def process_subagent_batch(
        self,
        tool_calls: List[ToolCallPayload],
        *,
        messages: List[ChatMessage],
        turn_id: int,
        iteration: int,
        on_tool_call: Optional[Callable],
        on_event,
        tools_used: Optional[List[str]],
    ) -> int:
        """Process one consecutive run_subagent batch with per-turn-capped fan-out."""
        resolved_tool_calls: List[_ResolvedSubagentToolCall] = []

        for tool_call in tool_calls:
            logging_arguments = self.parse_tool_arguments_for_logging(tool_call["arguments"])
            tool_call_id = tool_call["id"]
            if on_tool_call is not None:
                on_tool_call("run_subagent", logging_arguments)

            self.logger.log_tool_call(
                turn_id=turn_id,
                iteration=iteration,
                tool_name="run_subagent",
                arguments=logging_arguments,
                tool_call_id=tool_call_id,
            )

            if tools_used is not None and "run_subagent" not in tools_used:
                tools_used.append("run_subagent")

            if self.subagent_manager is None:
                resolved_tool_calls.append(
                    _ResolvedSubagentToolCall(
                        tool_call_id=tool_call_id,
                        error_payload={"error": "Subagent runtime is not available"},
                    )
                )
                continue

            try:
                arguments = json.loads(tool_call["arguments"])
            except json.JSONDecodeError as exc:
                resolved_tool_calls.append(
                    _ResolvedSubagentToolCall(
                        tool_call_id=tool_call_id,
                        error_payload={"error": f"Invalid JSON in tool arguments: {exc}"},
                    )
                )
                continue

            if not isinstance(arguments, dict):
                resolved_tool_calls.append(
                    _ResolvedSubagentToolCall(
                        tool_call_id=tool_call_id,
                        error_payload={"error": "Tool arguments must decode to a JSON object"},
                    )
                )
                continue

            try:
                request = self.subagent_manager.build_subagent_request(arguments)
            except ValueError as exc:
                resolved_tool_calls.append(
                    _ResolvedSubagentToolCall(
                        tool_call_id=tool_call_id,
                        error_payload={"error": str(exc)},
                    )
                )
                continue

            resolved_tool_calls.append(
                _ResolvedSubagentToolCall(
                    tool_call_id=tool_call_id,
                    request=request,
                )
            )

        valid_requests = [
            resolved_tool_call.request
            for resolved_tool_call in resolved_tool_calls
            if resolved_tool_call.request is not None
        ]
        subagent_results: List[Any] = []
        if self.subagent_manager is not None and valid_requests:
            subagent_results = self.subagent_manager.run_subagents(
                self.parent_agent,
                valid_requests,
                parent_turn_id=turn_id,
                on_event=on_event,
            )
        subagent_results_iter = iter(subagent_results)

        for resolved_tool_call in resolved_tool_calls:
            result_payload = (
                resolved_tool_call.error_payload
                if resolved_tool_call.error_payload is not None
                else next(subagent_results_iter).to_payload()
            )
            messages.append(
                self.build_tool_result_message(
                    resolved_tool_call.tool_call_id,
                    result_payload,
                )
            )
            self.logger.log_tool_result(
                turn_id=turn_id,
                iteration=iteration,
                tool_name="run_subagent",
                result=result_payload,
                tool_call_id=resolved_tool_call.tool_call_id,
            )

        return len(tool_calls)

    def process_tool_calls(
        self,
        tool_calls: List[ToolCallPayload],
        *,
        messages: List[ChatMessage],
        turn_id: int,
        iteration: int,
        on_tool_call: Optional[Callable] = None,
        on_event=None,
        tools_used: Optional[List[str]] = None,
        skills_used: Optional[List[str]] = None,
    ) -> ToolBatchOutcome:
        """Process multiple tool calls and append replayable tool results."""
        processed_count = 0
        index = 0
        while index < len(tool_calls):
            current_tool_call = tool_calls[index]
            tool_name = current_tool_call["name"]
            tool_call_id = current_tool_call["id"]
            raw_arguments = current_tool_call["arguments"]

            if tool_name == "run_subagent":
                subagent_batch: List[ToolCallPayload] = []
                while index < len(tool_calls) and tool_calls[index]["name"] == "run_subagent":
                    subagent_batch.append(tool_calls[index])
                    index += 1
                processed_count += self.process_subagent_batch(
                    subagent_batch,
                    messages=messages,
                    turn_id=turn_id,
                    iteration=iteration,
                    on_tool_call=on_tool_call,
                    on_event=on_event,
                    tools_used=tools_used,
                )
                continue

            processed_count += 1
            if tools_used is not None and tool_name not in tools_used:
                tools_used.append(tool_name)

            logging_arguments = self.parse_tool_arguments_for_logging(raw_arguments)

            if tool_name == "load_skill":
                self.emit_skill_event(
                    turn_id,
                    "tool_load_requested",
                    skill_name=logging_arguments.get("skill_name"),
                    iteration=iteration,
                )
                self.emit_turn_event(
                    on_event,
                    "skill_load_requested",
                    iteration=iteration,
                    skill_name=logging_arguments.get("skill_name"),
                )

            if on_tool_call:
                on_tool_call(tool_name, logging_arguments)

            self.emit_turn_event(
                on_event,
                "tool_call_started",
                iteration=iteration,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                arguments=logging_arguments,
            )
            started_at = perf_counter()

            self.logger.log_tool_call(
                turn_id=turn_id,
                iteration=iteration,
                tool_name=tool_name,
                arguments=logging_arguments,
                tool_call_id=tool_call_id,
            )

            decoded_arguments, decode_error = self._decode_tool_arguments(raw_arguments)
            if decode_error is not None:
                self._record_tool_completion(
                    messages=messages,
                    turn_id=turn_id,
                    iteration=iteration,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    logging_arguments=logging_arguments,
                    result_content=decode_error,
                    duration_s=perf_counter() - started_at,
                    on_event=on_event,
                    append_message=True,
                )
                index += 1
                continue

            parsed_args = decoded_arguments

            if tool_name == "submit_plan":
                result_content = self.execute_submit_plan_tool(parsed_args)
                self._record_tool_completion(
                    messages=messages,
                    turn_id=turn_id,
                    iteration=iteration,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    logging_arguments=logging_arguments,
                    result_content=result_content,
                    duration_s=0.0,
                    on_event=on_event,
                    append_message="error" in result_content,
                )
                if "error" in result_content:
                    index += 1
                    continue

                submitted_plan = self.context.get_current_plan()
                submitted_report = (
                    submitted_plan.report
                    if submitted_plan is not None and submitted_plan.report
                    else str(result_content.get("report", ""))
                )
                submitted_summary = (
                    submitted_plan.summary
                    if submitted_plan is not None and submitted_plan.summary
                    else str(result_content.get("summary", ""))
                )
                self.logger.log_plan_event(
                    turn_id=turn_id,
                    stage="submitted",
                    plan_id=submitted_plan.plan_id if submitted_plan is not None else None,
                    status=submitted_plan.status if submitted_plan is not None else "ready_for_review",
                    file_path=submitted_plan.file_path if submitted_plan is not None else None,
                    summary=submitted_summary,
                )
                self.emit_turn_event(
                    on_event,
                    "plan_submitted",
                    iteration=iteration,
                    summary=submitted_summary,
                    plan_id=submitted_plan.plan_id if submitted_plan is not None else None,
                    file_path=submitted_plan.file_path if submitted_plan is not None else None,
                )
                return ToolBatchOutcome(
                    processed_count=processed_count,
                    terminal_response=submitted_report,
                )

            tool_result_message, result_content = self.execute_standard_tool_call(current_tool_call, parsed_args)
            messages.append(tool_result_message)
            self._record_tool_completion(
                messages=messages,
                turn_id=turn_id,
                iteration=iteration,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                logging_arguments=logging_arguments,
                result_content=result_content,
                duration_s=perf_counter() - started_at,
                on_event=on_event,
                append_message=False,
            )
            if tool_name == "load_skill":
                skill_event_name = "tool_load_succeeded"
                cli_event_name = "skill_load_succeeded"
                event_details = {
                    "skill_name": parsed_args.get("skill_name"),
                    "iteration": iteration,
                }
                if "error" in result_content:
                    skill_event_name = "tool_load_failed"
                    cli_event_name = "skill_load_failed"
                    event_details["error"] = result_content["error"]
                elif skills_used is not None:
                    skill_name = parsed_args.get("skill_name")
                    if skill_name and skill_name not in skills_used:
                        skills_used.append(skill_name)
                self.emit_skill_event(turn_id, skill_event_name, **event_details)
                cli_details = {key: value for key, value in event_details.items() if key != "iteration"}
                self.emit_turn_event(on_event, cli_event_name, iteration=iteration, **cli_details)
            if tool_name == "write_plan" and "error" not in result_content:
                current_plan = self.context.get_current_plan()
                self.logger.log_plan_event(
                    turn_id=turn_id,
                    stage="written",
                    plan_id=current_plan.plan_id if current_plan is not None else None,
                    status=current_plan.status if current_plan is not None else "draft",
                    file_path=current_plan.file_path if current_plan is not None else None,
                )
                self.emit_turn_event(
                    on_event,
                    "plan_written",
                    iteration=iteration,
                    plan_id=current_plan.plan_id if current_plan is not None else None,
                    file_path=current_plan.file_path if current_plan is not None else None,
                )
            index += 1

        return ToolBatchOutcome(processed_count=processed_count)

    def _decode_tool_arguments(
        self,
        raw_arguments: str,
    ) -> tuple[Dict[str, Any] | None, ToolResultPayload | None]:
        """Decode tool arguments into a JSON object or a standard error payload."""
        try:
            decoded_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            return None, {"error": f"Invalid JSON in tool arguments: {exc}"}

        if not isinstance(decoded_arguments, dict):
            return None, {"error": "Tool arguments must decode to a JSON object"}

        return decoded_arguments, None

    def _record_tool_completion(
        self,
        *,
        messages: List[ChatMessage],
        turn_id: int,
        iteration: int,
        tool_name: str,
        tool_call_id: str,
        logging_arguments: Dict[str, Any],
        result_content: ToolResultPayload,
        duration_s: float,
        on_event,
        append_message: bool,
    ) -> None:
        """Log, optionally append, and emit the final event for one tool result."""
        if append_message:
            messages.append(self.build_tool_result_message(tool_call_id, result_content))

        self.logger.log_tool_result(
            turn_id=turn_id,
            iteration=iteration,
            tool_name=tool_name,
            result=result_content,
            tool_call_id=tool_call_id,
        )
        result_preview, result_body = build_tool_result_preview(tool_name, result_content)
        self.emit_turn_event(
            on_event,
            "tool_call_finished",
            iteration=iteration,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=logging_arguments,
            success="error" not in result_content,
            duration_s=duration_s,
            error=result_content.get("error"),
            result_preview=result_preview,
            result_body=result_body,
        )
