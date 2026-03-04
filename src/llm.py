"""LLM integration for Nano-Claw."""

import os
from typing import Optional, List, Dict, TYPE_CHECKING, Any
from openai import OpenAI
from src.config import config
from src.message_types import ChatMessage, ToolCallPayload
from src.tools import REQUEST_KIND_AGENT_TURN

if TYPE_CHECKING:
    from src.logger import SessionLogger
    from src.metrics import LLMMetrics


class LLMClient:
    """Handles communication with OpenAI-compatible APIs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = None,
        logger: Optional['SessionLogger'] = None,
        runtime_config=None,
    ):
        """Initialize the LLM client.

        Args:
            api_key: API key (defaults to provider-specific env var)
            model: Model name (defaults to MODEL env var or provider default)
            base_url: Custom base URL (defaults to BASE_URL env var)
            provider: Provider name for env var lookup (defaults to LLM_PROVIDER env var)
            logger: Optional SessionLogger instance for logging requests/responses
        """
        self.runtime_config = runtime_config or config
        # Determine provider
        provider = provider or self.runtime_config.llm.provider

        # Get API key based on provider
        if api_key is None:
            api_key = self._get_api_key(provider)

        if not api_key and provider not in ("ollama", "local"):
            raise ValueError(
                f"API key required for provider '{provider}'. "
                f"Set {self._get_api_key_env_var(provider)}"
            )

        # Get base URL
        if base_url is None:
            base_url = self.runtime_config.llm.base_url
            # Set default URLs for known providers
            if not base_url and provider == "ollama":
                base_url = "http://localhost:11434/v1"

        # Get model name with provider defaults
        if model is None:
            model = self.runtime_config.llm.model
            if not model:
                model = self._get_default_model(provider)

        self.model = model
        self.provider = provider
        self.base_url = base_url
        self.logger = logger

        # Initialize OpenAI client (works with any OpenAI-compatible API)
        client_kwargs = {"api_key": api_key or "not-needed"}  # Ollama doesn't need API key
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = OpenAI(**client_kwargs)

    def _get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for the given provider.

        Priority:
        1. Environment variable (for override)
        2. config.llm.api_key (from config.yaml)
        3. None (not set)
        """
        # First check environment variable (allows override)
        env_var = self._get_api_key_env_var(provider)
        env_key = os.environ.get(env_var)

        if env_key:
            return env_key

        # Fall back to config.yaml
        return self.runtime_config.llm.api_key

    def _get_api_key_env_var(self, provider: str) -> str:
        """Get the environment variable name for API key."""
        key_map = {
            "openai": "OPENAI_API_KEY",
            "azure": "AZURE_API_KEY",
            "custom": "CUSTOM_API_KEY",
        }
        return key_map.get(provider, "API_KEY")

    def _get_default_model(self, provider: str) -> str:
        """Get default model for the given provider."""
        defaults = {
            "openai": "gpt-4",
            "azure": "gpt-4",
            "ollama": "llama2",
            "local": "llama2",
            "custom": "gpt-4",
        }
        return defaults.get(provider, "gpt-4")

    def _estimate_prompt_tokens(self, messages: List[ChatMessage]) -> int:
        """Estimate prompt tokens from messages.

        Uses a rough character-based estimate (char_count / 4) as a fallback
        when the API doesn't return usage information. This is approximate
        but sufficient for display purposes.

        Args:
            messages: List of message dicts with role and content

        Returns:
            Estimated token count
        """
        import json

        # Serialize messages to JSON to get a rough character count
        # This includes role, content, and JSON structure overhead
        text = json.dumps(messages)

        # Rough estimate: ~4 characters per token for English text
        # This is not exact but provides a reasonable approximation
        return max(1, len(text) // 4)

    def _apply_usage_metrics(self, metrics: 'LLMMetrics', usage: Any) -> bool:
        """Apply usage metrics if the usage payload looks valid."""
        if usage is None:
            return False

        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if not all(isinstance(value, int) for value in (prompt_tokens, completion_tokens, total_tokens)):
            return False

        metrics.prompt_tokens = prompt_tokens
        metrics.completion_tokens = completion_tokens
        metrics.total_tokens = total_tokens

        prompt_details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(prompt_details, "cached_tokens", 0) if prompt_details is not None else 0
        metrics.cached_tokens = cached if isinstance(cached, int) else 0
        return True

    def _build_chat_kwargs(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        stream: bool = False
    ) -> Dict:
        """Build kwargs for chat.completions.create API call.

        Args:
            messages: Message list
            tools: Optional tool schemas
            stream: Whether to stream response

        Returns:
            kwargs dict for API call
        """
        kwargs = {"model": self.model, "messages": messages, "stream": stream}

        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = True

        return kwargs

    def _metrics_to_dict(self, metrics: 'LLMMetrics') -> Dict[str, Any]:
        """Serialize metrics for logging."""
        return {
            "prompt_tokens": metrics.prompt_tokens,
            "completion_tokens": metrics.completion_tokens,
            "total_tokens": metrics.total_tokens,
            "cached_tokens": metrics.cached_tokens,
            "ttft": round(metrics.ttft, 6),
            "duration": round(metrics.duration, 6),
            "tokens_per_second": round(metrics.tokens_per_second, 6),
            "tpot": round(metrics.tpot, 6),
        }

    def _safe_model_dump(self, value: Any) -> Optional[Dict[str, Any]]:
        """Try to use model_dump(mode='json') when available."""
        model_dump = getattr(value, "model_dump", None)
        if not callable(model_dump):
            return None

        for kwargs in ({"mode": "json"}, {}):
            try:
                dumped = model_dump(**kwargs)
            except Exception:
                continue
            if isinstance(dumped, dict):
                return dumped
        return None

    def _serialize_usage(self, usage: Any) -> Optional[Dict[str, Any]]:
        """Serialize a usage object into plain JSON-friendly data."""
        dumped = self._safe_model_dump(usage)
        if dumped is not None:
            return dumped
        if usage is None:
            return None

        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
        cached_tokens = getattr(prompt_tokens_details, "cached_tokens", None) if prompt_tokens_details is not None else None

        payload = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "prompt_tokens_details": {
                "cached_tokens": cached_tokens,
            } if cached_tokens is not None else None,
        }
        return payload

    def _serialize_tool_call(self, tool_call: Any) -> Dict[str, Any]:
        """Serialize a tool call into plain JSON-friendly data."""
        dumped = self._safe_model_dump(tool_call)
        if dumped is not None:
            return dumped

        function = getattr(tool_call, "function", None)
        return {
            "id": getattr(tool_call, "id", None),
            "type": getattr(tool_call, "type", "function"),
            "function": {
                "name": getattr(function, "name", None),
                "arguments": getattr(function, "arguments", None),
            },
        }

    def _serialize_message(self, message: Any) -> Dict[str, Any]:
        """Serialize an assistant message from the SDK response."""
        dumped = self._safe_model_dump(message)
        if dumped is not None:
            return dumped

        tool_calls = getattr(message, "tool_calls", None)
        return {
            "role": getattr(message, "role", None),
            "content": getattr(message, "content", None),
            "tool_calls": [self._serialize_tool_call(tool_call) for tool_call in tool_calls] if tool_calls else None,
        }

    def _serialize_choice(self, choice: Any) -> Dict[str, Any]:
        """Serialize a response choice."""
        dumped = self._safe_model_dump(choice)
        if dumped is not None:
            return dumped

        return {
            "index": getattr(choice, "index", 0),
            "finish_reason": getattr(choice, "finish_reason", None),
            "message": self._serialize_message(getattr(choice, "message", None)),
        }

    def _serialize_response_payload(self, response: Any) -> Dict[str, Any]:
        """Serialize the full response object for timeline logging."""
        dumped = self._safe_model_dump(response)
        if dumped is not None:
            return dumped

        choices = getattr(response, "choices", None) or []
        return {
            "id": getattr(response, "id", None),
            "object": getattr(response, "object", "chat.completion"),
            "created": getattr(response, "created", None),
            "model": getattr(response, "model", self.model),
            "choices": [self._serialize_choice(choice) for choice in choices],
            "usage": self._serialize_usage(getattr(response, "usage", None)),
        }

    def _build_stream_response_payload(
        self,
        *,
        role: str,
        content: str,
        tool_calls: List[Dict[str, Any]],
        finish_reason: Optional[str],
        chunk_count: int,
    ) -> Dict[str, Any]:
        """Build a reconstructed response payload for streaming logs."""
        return {
            "object": "chat.completion.stream.reconstructed",
            "model": self.model,
            "provider": self.provider,
            "stream": True,
            "chunk_count": chunk_count,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {
                        "role": role,
                        "content": content,
                        "tool_calls": [
                            {
                                "id": tool_call["id"],
                                "type": "function",
                                "function": {
                                    "name": tool_call["name"],
                                    "arguments": tool_call["arguments"],
                                },
                            }
                            for tool_call in tool_calls
                        ] if tool_calls else None,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": self._current_metrics.prompt_tokens,
                "completion_tokens": self._current_metrics.completion_tokens,
                "total_tokens": self._current_metrics.total_tokens,
                "prompt_tokens_details": {
                    "cached_tokens": self._current_metrics.cached_tokens,
                },
            },
        }

    def chat(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        log_context: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Send messages and get response with tool calls.

        Args:
            messages: Conversation history in OpenAI format
            tools: Optional list of tool schemas for function calling

        Returns:
            Tuple of (response_dict, metrics)
        """
        from src.metrics import LLMMetrics

        metrics = LLMMetrics(
            model=self.model,
            provider=self.provider,
            request_type="non-streaming"
        )

        kwargs = self._build_chat_kwargs(messages, tools, stream=False)

        if self.logger and log_context:
            self.logger.log_llm_request(
                turn_id=log_context["turn_id"],
                iteration=log_context["iteration"],
                request_payload=kwargs,
                provider=self.provider,
                model=self.model,
                stream=bool(log_context.get("stream", False)),
                request_kind=log_context.get("request_kind", REQUEST_KIND_AGENT_TURN),
            )

        response = self.client.chat.completions.create(**kwargs)

        # Extract usage information
        if not self._apply_usage_metrics(metrics, getattr(response, "usage", None)):
            # Fallback: estimate tokens when API doesn't return usage
            metrics.prompt_tokens = self._estimate_prompt_tokens(messages)
            # For non-streaming, we can't accurately count completion tokens without usage
            # Set them to 0 and let total_tokens be just prompt_tokens
            metrics.completion_tokens = 0
            metrics.total_tokens = metrics.prompt_tokens

        metrics.finish()

        message = response.choices[0].message

        result = {
            "role": message.role,
            "content": message.content or "",
        }

        # Extract tool calls if present
        if message.tool_calls:
            result["tool_calls"] = []
            for tool_call in message.tool_calls:
                result["tool_calls"].append({
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments
                })

        # Log response
        if self.logger and log_context:
            self.logger.log_llm_response(
                turn_id=log_context["turn_id"],
                iteration=log_context["iteration"],
                response_payload=self._serialize_response_payload(response),
                provider=self.provider,
                model=self.model,
                stream=bool(log_context.get("stream", False)),
                metrics=self._metrics_to_dict(metrics),
                request_kind=log_context.get("request_kind", REQUEST_KIND_AGENT_TURN),
            )

        return result, metrics

    def chat_stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict]] = None,
        log_context: Optional[Dict[str, Any]] = None,
    ):
        """Stream chat completion responses token-by-token.

        Args:
            messages: Conversation history in OpenAI format
            tools: Optional list of tool schemas for function calling

        Yields:
            Dicts with: {"delta": str, "role": str, "tool_calls": Optional[List], "finish_reason": str}
        """
        from src.metrics import LLMMetrics

        kwargs = self._build_chat_kwargs(messages, tools, stream=True)

        if self.logger and log_context:
            self.logger.log_llm_request(
                turn_id=log_context["turn_id"],
                iteration=log_context["iteration"],
                request_payload=kwargs,
                provider=self.provider,
                model=self.model,
                stream=bool(log_context.get("stream", True)),
                request_kind=log_context.get("request_kind", REQUEST_KIND_AGENT_TURN),
            )

        # Create metrics tracker
        self._current_metrics = LLMMetrics(
            model=self.model,
            provider=self.provider,
            request_type="streaming"
        )

        # Start timing right before API call for accurate TTFT
        from time import perf_counter
        self._current_metrics.start_time = perf_counter()

        response = self.client.chat.completions.create(**kwargs)

        # Collect for logging
        full_content = ""
        full_role = "assistant"
        accumulated_tool_calls = {}
        first_token_marked = False
        final_finish_reason = None
        chunk_count = 0

        for chunk in response:
            chunk_count += 1
            choice = chunk.choices[0]
            delta = choice.delta
            finish_reason = choice.finish_reason

            # Extract role (usually in first chunk)
            if delta.role:
                full_role = delta.role
                yield {"role": delta.role}

            # Extract content tokens
            if delta.content:
                full_content += delta.content

                # Mark first token arrival
                if not first_token_marked:
                    self._current_metrics.mark_first_token()
                    first_token_marked = True

                # Record token timestamp
                self._current_metrics.add_token_timestamp()

                yield {"delta": delta.content, "role": "assistant"}

            # Extract tool calls (streamed in chunks)
            if delta.tool_calls:
                for tool_call_chunk in delta.tool_calls:
                    index = tool_call_chunk.index

                    if index not in accumulated_tool_calls:
                        accumulated_tool_calls[index] = {
                            "index": index,
                            "id": tool_call_chunk.id or f"call_{index}",
                            "name": "",
                            "arguments": ""
                        }

                    tc = accumulated_tool_calls[index]

                    if tool_call_chunk.function:
                        if tool_call_chunk.function.name:
                            tc["name"] = tool_call_chunk.function.name
                        if tool_call_chunk.function.arguments:
                            tc["arguments"] += tool_call_chunk.function.arguments

                # Mark first token arrival for tool calls (if not already marked)
                if not first_token_marked:
                    self._current_metrics.mark_first_token()
                    first_token_marked = True

                # Record token timestamp for each tool call chunk
                self._current_metrics.add_token_timestamp()

            # Signal when complete
            if finish_reason:
                final_finish_reason = finish_reason
                yield {"finish_reason": finish_reason}

                # Extract usage from final chunk if available
                self._apply_usage_metrics(self._current_metrics, getattr(chunk, "usage", None))

        # Always call finish() to record end time, regardless of usage availability
        self._current_metrics.finish()

        # If no usage info was found in the stream, estimate it
        if self._current_metrics.prompt_tokens == 0:
            self._current_metrics.completion_tokens = self._current_metrics.token_count
            self._current_metrics.prompt_tokens = self._estimate_prompt_tokens(messages)
            self._current_metrics.total_tokens = self._current_metrics.prompt_tokens + self._current_metrics.completion_tokens

        # Log complete response
        tool_calls_list = [
            {
                "id": tool_call["id"],
                "name": tool_call["name"],
                "arguments": tool_call["arguments"],
            }
            for _, tool_call in sorted(accumulated_tool_calls.items(), key=lambda item: item[0])
        ]

        if self.logger and log_context:
            self.logger.log_llm_response(
                turn_id=log_context["turn_id"],
                iteration=log_context["iteration"],
                response_payload=self._build_stream_response_payload(
                    role=full_role,
                    content=full_content,
                    tool_calls=tool_calls_list,
                    finish_reason=final_finish_reason,
                    chunk_count=chunk_count,
                ),
                provider=self.provider,
                model=self.model,
                stream=bool(log_context.get("stream", True)),
                metrics=self._metrics_to_dict(self._current_metrics),
                request_kind=log_context.get("request_kind", REQUEST_KIND_AGENT_TURN),
            )

        # Store tool calls for retrieval by caller (eliminates need for duplicate API call)
        self._last_stream_tool_calls = tool_calls_list if tool_calls_list else []

    def get_stream_metrics(self) -> Optional['LLMMetrics']:
        """Get metrics from the most recent stream request."""
        return getattr(self, '_current_metrics', None)

    def get_stream_tool_calls(self) -> List[ToolCallPayload]:
        """Get tool calls from the most recent stream request.

        Returns:
            List of tool call dicts with id, name, arguments
        """
        return getattr(self, '_last_stream_tool_calls', [])
