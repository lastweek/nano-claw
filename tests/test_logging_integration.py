"""Test logging integration between Agent and LLMClient."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

from src.agent import Agent
from src.config import Config
from src.context import Context
from src.llm import LLMClient
from src.tools import ToolRegistry
from src.tools.read import ReadTool


class TestLoggingIntegration:
    """Test that logging is properly integrated across components."""

    def test_default_agent_logger_uses_test_runtime_root(self, temp_dir, monkeypatch):
        """Default Agent logger should resolve under the pytest runtime root."""
        test_root = (temp_dir / "runtime").resolve()
        monkeypatch.setenv("BABYCLAW_TEST", "true")
        monkeypatch.setenv("BABYCLAW_TEST_ROOT", str(test_root))
        monkeypatch.delenv("LOG_DIR", raising=False)
        runtime_config = Config()

        context = Context.create(cwd=str(temp_dir))
        tools = ToolRegistry()
        tools.register(ReadTool())
        with patch("src.llm.OpenAI"):
            llm_client = LLMClient(provider="ollama")
        agent = Agent(llm_client, tools, context, runtime_config=runtime_config)

        session_dir = agent.logger.ensure_session_dir()
        assert session_dir.is_relative_to(test_root / "sessions")
        assert not session_dir.is_relative_to((Path.home() / ".babyclaw").resolve())

    def test_agent_shares_logger_with_llm_client(self, temp_dir, monkeypatch):
        """Agent should inject its logger into LLMClient."""
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
        tools.register(ReadTool())
        with patch("src.llm.OpenAI"):
            llm_client = LLMClient(provider="ollama", runtime_config=runtime_config)
        agent = Agent(llm_client, tools, context, runtime_config=runtime_config)

        assert llm_client.logger is not None
        assert llm_client.logger is agent.logger
        assert llm_client.logger.session_id == context.session_id

    def test_agent_run_creates_session_directory_with_llm_and_events(self, temp_dir, monkeypatch):
        """A normal agent run should create session.json, llm.log, and events.jsonl."""
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
        tools.register(ReadTool())
        with patch("src.llm.OpenAI"):
            llm_client = LLMClient(provider="ollama", runtime_config=runtime_config)
        agent = Agent(llm_client, tools, context, runtime_config=runtime_config)

        mock_response = Mock()
        mock_response.usage = None
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.role = "assistant"
        mock_response.choices[0].message.content = "Hello!"
        mock_response.choices[0].message.tool_calls = None

        with patch.object(llm_client.client.chat.completions, "create", return_value=mock_response):
            response = agent.run("test")

        assert response == "Hello!"
        agent.logger.close()

        session_dir = agent.logger.session_dir
        assert session_dir is not None
        session = json.loads((session_dir / "session.json").read_text())
        assert session["turn_count"] == 1
        assert session["llm_call_count"] == 1
        assert session["timeline_format_version"] == 2
        assert session["primary_debug_log"] == "llm.log"

        llm_log = (session_dir / "llm.log").read_text()
        assert "TURN START" in llm_log
        assert "LLM REQUEST" in llm_log
        assert "REQUEST JSON" in llm_log
        assert "\"messages\"" in llm_log
        assert "LLM RESPONSE" in llm_log
        assert "RESPONSE JSON" in llm_log
        assert "TURN END" in llm_log
        assert "\"content\": \"Hello!\"" in llm_log

        events = [
            json.loads(line)
            for line in (session_dir / "events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        kinds = [event["kind"] for event in events]
        assert "turn_started" in kinds
        assert "turn_completed" in kinds
