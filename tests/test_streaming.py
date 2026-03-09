"""Test streaming functionality."""

import json
from unittest.mock import Mock, patch

from src.agent import Agent
from src.config import Config
from src.context import Context
from src.llm import LLMClient
from src.logger import SessionLogger
from src.tools import ToolRegistry


class TestStreaming:
    """Test streaming functionality."""

    def test_llm_chat_stream_yields_tokens(self):
        """chat_stream should yield role, deltas, and finish_reason."""
        llm_client = LLMClient(provider="ollama")

        mock_chunks = [
            Mock(choices=[Mock(delta=Mock(role="assistant", content="Hello", tool_calls=None), finish_reason=None)], usage=None),
            Mock(choices=[Mock(delta=Mock(role=None, content=" world", tool_calls=None), finish_reason=None)], usage=None),
            Mock(choices=[Mock(delta=Mock(role=None, content="!", tool_calls=None), finish_reason="stop")], usage=None),
        ]

        with patch.object(llm_client.client.chat.completions, "create", return_value=iter(mock_chunks)):
            tokens = list(llm_client.chat_stream([{"role": "user", "content": "test"}]))

        assert len(tokens) == 5
        assert tokens[0]["role"] == "assistant"
        assert tokens[1]["delta"] == "Hello"
        assert tokens[2]["delta"] == " world"
        assert tokens[3]["delta"] == "!"
        assert tokens[4]["finish_reason"] == "stop"

    def test_llm_chat_stream_with_logger(self, temp_dir):
        """Streaming should write one request and one reconstructed response to llm.log."""
        logger = SessionLogger("test-session", log_dir=str(temp_dir), enabled=True)
        logger.start_session(
            cwd=str(temp_dir),
            provider="ollama",
            model="llama2",
            base_url="http://localhost:11434/v1",
            streaming_enabled=True,
        )
        turn_id = logger.start_turn(raw_user_input="hello", normalized_user_input="hello")
        llm_client = LLMClient(provider="ollama", logger=logger)

        mock_chunks = [
            Mock(choices=[Mock(delta=Mock(role="assistant", content="Hi", tool_calls=None), finish_reason=None)], usage=None),
            Mock(choices=[Mock(delta=Mock(role=None, content=" there", tool_calls=None), finish_reason="stop")], usage=None),
        ]

        with patch.object(llm_client.client.chat.completions, "create", return_value=iter(mock_chunks)):
            list(
                llm_client.chat_stream(
                    [{"role": "user", "content": "hello"}],
                    log_context={"turn_id": turn_id, "iteration": 0, "stream": True},
                )
            )

        logger.finish_turn(turn_id, "Hi there", [], status="completed")
        logger.close()

        session_dir = logger.session_dir
        assert session_dir is not None
        llm_log = (session_dir / "llm.log").read_text()
        assert llm_log.count("LLM REQUEST") == 1
        assert llm_log.count("LLM RESPONSE") == 1
        assert "REQUEST JSON" in llm_log
        assert "RESPONSE JSON" in llm_log
        assert "\"chunk_count\": 2" in llm_log
        assert "\"content\": \"Hi there\"" in llm_log

    def test_agent_run_stream_yields_tokens(self, temp_dir, monkeypatch):
        """agent.run_stream should yield text and emit activity events."""
        runtime_config = Config(
            {
                "logging": {
                    "enabled": True,
                    "async_mode": False,
                    "log_dir": str(temp_dir),
                    "buffer_size": 1,
                },
                "mcp": {"servers": []},
            }
        )
        context = Context.create(cwd=str(temp_dir))
        tools = ToolRegistry()
        llm_client = LLMClient(provider="ollama", runtime_config=runtime_config)
        agent = Agent(llm_client, tools, context, runtime_config=runtime_config)

        mock_chunks = [
            Mock(choices=[Mock(delta=Mock(role="assistant", content="Test", tool_calls=None), finish_reason=None)], usage=None),
            Mock(choices=[Mock(delta=Mock(role=None, content=" response", tool_calls=None), finish_reason="stop")], usage=None),
        ]

        with patch.object(llm_client.client.chat.completions, "create", return_value=iter(mock_chunks)):
            events = []
            tokens = list(agent.run_stream("hello", on_event=lambda event: events.append(event)))

        assert len(tokens) == 2
        assert "".join(tokens) == "Test response"
        assert [event.kind for event in events] == [
            "llm_call_started",
            "llm_call_finished",
            "turn_completed",
        ]
        assert events[1].details["result_kind"] == "final_answer"

        agent.logger.close()
        session_dir = agent.logger.session_dir
        assert session_dir is not None
        llm_log = (session_dir / "llm.log").read_text()
        assert llm_log.count("LLM RESPONSE") == 1
        assert "\"content\": \"Test response\"" in llm_log
        events = [
            json.loads(line)
            for line in (session_dir / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert any(event["kind"] == "turn_completed" for event in events)
