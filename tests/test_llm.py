"""Test LLMClient configuration."""

import pytest
import importlib
import sys
from unittest.mock import Mock, patch
from src.llm import LLMClient


class TestLLMClient:
    """Test LLMClient initialization and configuration."""

    def _reload_config_and_modules(self):
        """Helper to reload config and dependent modules.

        This is needed because config is loaded at import time and cached.
        When we change env vars in tests, we need to reload to pick up changes.
        """
        from src.config import Config
        import os
        # Enable test mode to skip loading config.yaml
        os.environ['BABYCLAW_TEST'] = 'true'
        Config.reload()
        # Reload modules that import config
        if 'src.llm' in sys.modules:
            importlib.reload(sys.modules['src.llm'])
        if 'src.logger' in sys.modules:
            importlib.reload(sys.modules['src.logger'])

    def test_openai_provider_with_key(self, monkeypatch):
        """Test OpenAI provider initialization."""
        self._reload_config_and_modules()
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        client = LLMClient(provider="openai")
        assert client.provider == "openai"
        assert client.model == "gpt-4"
        assert client.client is not None

    def test_custom_provider_with_base_url(self, monkeypatch):
        """Test custom provider with base URL."""
        monkeypatch.setenv("CUSTOM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.test.com/v1")
        self._reload_config_and_modules()
        client = LLMClient(provider="custom")
        assert client.provider == "custom"
        assert client.client is not None

    def test_ollama_provider_no_api_key(self, monkeypatch):
        """Test Ollama provider doesn't require API key."""
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        self._reload_config_and_modules()
        client = LLMClient(provider="ollama")
        assert client.provider == "ollama"
        assert client.model == "llama2"

    def test_default_base_url_for_ollama(self, monkeypatch):
        """Test Ollama gets default base URL."""
        monkeypatch.setenv("LLM_BASE_URL", "")  # Empty
        self._reload_config_and_modules()
        client = LLMClient(provider="ollama")
        # Client should be initialized with default Ollama URL
        assert client.provider == "ollama"

    def test_missing_api_key_raises_error(self, monkeypatch):
        """Test that missing API key raises ValueError."""
        self._reload_config_and_modules()
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key required"):
            LLMClient(provider="openai")

    def test_model_from_env(self, monkeypatch):
        """Test model can be set from environment."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "gpt-3.5-turbo")
        self._reload_config_and_modules()
        client = LLMClient(provider="openai")
        assert client.model == "gpt-3.5-turbo"

    def test_get_default_model(self):
        """Test _get_default_model returns correct defaults."""
        client = LLMClient.__new__(LLMClient)
        assert client._get_default_model("openai") == "gpt-4"
        assert client._get_default_model("ollama") == "llama2"
        assert client._get_default_model("azure") == "gpt-4"

    def test_get_api_key_env_var(self):
        """Test _get_api_key_env_var mapping."""
        client = LLMClient.__new__(LLMClient)
        assert client._get_api_key_env_var("openai") == "OPENAI_API_KEY"
        assert client._get_api_key_env_var("azure") == "AZURE_API_KEY"
        assert client._get_api_key_env_var("custom") == "CUSTOM_API_KEY"

    def test_stream_tool_calls_preserve_model_index_order(self, monkeypatch):
        """Streamed tool calls should be returned in the original tool index order."""
        monkeypatch.setenv("BABYCLAW_TEST", "true")
        self._reload_config_and_modules()
        client = LLMClient(provider="ollama")

        tool_call_a = Mock(index=0, id="call_b")
        tool_call_a.function = Mock(name="read_file", arguments='{"file_path":"a.txt"}')
        tool_call_b = Mock(index=1, id="call_a")
        tool_call_b.function = Mock(name="read_file", arguments='{"file_path":"b.txt"}')
        mock_chunks = [
            Mock(choices=[Mock(delta=Mock(role="assistant", content=None, tool_calls=[tool_call_b, tool_call_a]), finish_reason=None)], usage=None),
            Mock(choices=[Mock(delta=Mock(role=None, content=None, tool_calls=None), finish_reason="tool_calls")], usage=None),
        ]

        with patch.object(client.client.chat.completions, "create", return_value=iter(mock_chunks)):
            list(client.chat_stream([{"role": "user", "content": "inspect files"}]))

        assert [tool_call["id"] for tool_call in client.get_stream_tool_calls()] == ["call_b", "call_a"]
