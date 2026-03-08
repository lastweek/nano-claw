"""Internal message and tool-call types shared across the runtime."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


MessageRole = Literal["system", "user", "assistant", "tool"]


class ToolCallPayload(TypedDict):
    """Normalized tool call emitted by the LLM client."""

    id: str
    name: str
    arguments: str


class ToolCallFunctionMessage(TypedDict):
    """OpenAI-compatible assistant tool-call function payload."""

    name: str
    arguments: str


class AssistantToolCallMessage(TypedDict):
    """OpenAI-compatible assistant tool-call record."""

    id: str
    type: Literal["function"]
    function: ToolCallFunctionMessage


class ChatMessage(TypedDict, total=False):
    """Internal chat message shape used across the agent runtime."""

    role: MessageRole
    content: Any
    tool_call_id: str
    tool_calls: list[AssistantToolCallMessage]


class ToolResultPayload(TypedDict, total=False):
    """Common tool result payload shared across tool execution paths."""

    output: Any
    error: str
    capability_hint: dict[str, Any]
