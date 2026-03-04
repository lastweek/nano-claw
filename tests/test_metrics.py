"""Test metrics tracking functionality."""

import pytest
import time
from src.metrics import LLMMetrics


class TestLLMMetrics:
    """Test LLMMetrics dataclass."""

    def test_metrics_dataclass_initialization(self):
        """Test LLMMetrics dataclass initialization."""
        metrics = LLMMetrics(
            model="gpt-4",
            provider="openai",
            request_type="streaming"
        )
        assert metrics.model == "gpt-4"
        assert metrics.provider == "openai"
        assert metrics.request_type == "streaming"
        assert metrics.duration == 0.0  # Not finished
        assert metrics.ttft == 0.0
        assert metrics.prompt_tokens == 0
        assert metrics.completion_tokens == 0

    def test_metrics_finish(self):
        """Test that finish() sets end_time."""
        metrics = LLMMetrics(model="gpt-4", provider="openai")
        assert metrics.duration == 0.0

        time.sleep(0.01)
        metrics.finish()
        assert metrics.duration >= 0.01
        assert metrics.duration < 0.05

    def test_ttft_tracking(self):
        """Test TTFT (time to first token) tracking."""
        metrics = LLMMetrics()

        time.sleep(0.01)
        metrics.mark_first_token()

        assert metrics.ttft >= 0.01
        assert metrics.ttft < 0.05

    def test_ttft_only_set_once(self):
        """Test that TTFT is only set once on first call."""
        metrics = LLMMetrics()

        metrics.mark_first_token()
        first_ttft = metrics.ttft

        time.sleep(0.01)
        metrics.mark_first_token()

        assert metrics.ttft == first_ttft  # Should not change

    def test_tpot_calculation(self):
        """Test TPOT (time per output token) calculation."""
        metrics = LLMMetrics()

        metrics.add_token_timestamp()
        time.sleep(0.01)
        metrics.add_token_timestamp()
        time.sleep(0.01)
        metrics.add_token_timestamp()

        assert metrics.tpot >= 0.01
        assert metrics.token_count == 3
        # TPOT should be roughly total_time / (num_tokens - 1)
        # 3 tokens = 2 intervals, ~0.02s total / 2 = ~0.01s
        assert metrics.tpot < 0.05

    def test_tpot_with_single_token(self):
        """Test TPOT with single token returns 0."""
        metrics = LLMMetrics()

        metrics.add_token_timestamp()

        assert metrics.tpot == 0.0  # Need at least 2 tokens

    def test_tpot_with_no_tokens(self):
        """Test TPOT with no tokens returns 0."""
        metrics = LLMMetrics()

        assert metrics.tpot == 0.0

    def test_tokens_per_second(self):
        """Test tokens per second calculation."""
        metrics = LLMMetrics()
        metrics.completion_tokens = 100

        metrics.finish()
        time.sleep(0.01)

        # Recalculate end_time after sleep
        metrics.end_time = perf_counter()

        # With 100 tokens and ~0.01s, should be very high
        assert metrics.tokens_per_second > 0

    def test_tokens_per_second_with_no_tokens(self):
        """Test tokens per second with no tokens returns 0."""
        metrics = LLMMetrics()
        metrics.finish()

        assert metrics.tokens_per_second == 0.0

    def test_token_timestamps_accumulation(self):
        """Test that token timestamps are accumulated."""
        metrics = LLMMetrics()

        metrics.add_token_timestamp()
        metrics.add_token_timestamp()
        metrics.add_token_timestamp()

        assert metrics.token_count == 3

    def test_metrics_properties(self):
        """Test all computed properties."""
        metrics = LLMMetrics(
            model="gpt-4",
            provider="openai",
            request_type="streaming",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cached_tokens=20
        )

        assert metrics.prompt_tokens == 100
        assert metrics.completion_tokens == 50
        assert metrics.total_tokens == 150
        assert metrics.cached_tokens == 20


# Import at module level for tests
from time import perf_counter
