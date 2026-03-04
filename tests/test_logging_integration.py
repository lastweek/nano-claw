"""Test logging integration between Agent and LLMClient."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

from src.agent import Agent
from src.config import config
from src.context import Context
from src.llm import LLMClient
from src.tools import ToolRegistry
from src.tools.read import ReadTool


class TestLoggingIntegration:
    """Test that logging is properly integrated across components."""

    def test_agent_shares_logger_with_llm_client(self, temp_dir, monkeypatch):
        """Agent should inject its logger into LLMClient."""
        monkeypatch.setattr(config.logging, "log_dir", str(temp_dir))
        context = Context.create(cwd=str(temp_dir))
        tools = ToolRegistry()
        tools.register(ReadTool())
        llm_client = LLMClient(provider="ollama")
        agent = Agent(llm_client, tools, context)

        assert llm_client.logger is not None
        assert llm_client.logger is agent.logger
        assert llm_client.logger.session_id == context.session_id

    def test_agent_run_creates_session_directory_with_llm_and_events(self, temp_dir, monkeypatch):
        """A normal agent run should create session.json, llm.log, and events.jsonl."""
        monkeypatch.setattr(config.logging, "log_dir", str(temp_dir))
        context = Context.create(cwd=str(temp_dir))
        tools = ToolRegistry()
        tools.register(ReadTool())
        llm_client = LLMClient(provider="ollama")
        agent = Agent(llm_client, tools, context)

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
