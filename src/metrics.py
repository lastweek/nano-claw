"""Metrics tracking for LLM requests."""

from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class LLMMetrics:
    """Metrics for a single LLM request."""

    # Timing metrics
    start_time: float = field(default_factory=perf_counter)
    end_time: float = 0.0
    ttft: float = 0.0  # Time to first token (seconds)

    # Token timing (for streaming) - O(1) storage instead of O(n)
    first_token_time: float = 0.0
    last_token_time: float = 0.0
    token_count: int = 0

    # Usage metrics (from OpenAI response)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0

    # Request metadata
    model: str = ""
    provider: str = ""
    request_type: str = ""  # "streaming" or "non-streaming"
    iteration: int = 0

    @property
    def duration(self) -> float:
        """Total request duration in seconds."""
        return self.end_time - self.start_time if self.end_time > 0 else 0.0

    @property
    def tpot(self) -> float:
        """Time per output token (average) in seconds."""
        if self.token_count < 2:
            return 0.0
        # Use first_token_time if set, otherwise use start_time as fallback
        first_time = self.first_token_time if self.first_token_time > 0 else self.start_time
        return (self.last_token_time - first_time) / (self.token_count - 1)

    @property
    def tokens_per_second(self) -> float:
        """Tokens generated per second."""
        if self.duration == 0 or self.completion_tokens == 0:
            return 0.0
        return self.completion_tokens / self.duration

    def mark_first_token(self) -> None:
        """Mark when first token arrives (for TTFT)."""
        if self.ttft == 0.0:
            self.ttft = perf_counter() - self.start_time
            self.first_token_time = perf_counter()

    def add_token_timestamp(self) -> None:
        """Record timestamp for each token (for TPOT calculation)."""
        self.last_token_time = perf_counter()
        self.token_count += 1

    def finish(self) -> None:
        """Mark the end of the request."""
        self.end_time = perf_counter()
