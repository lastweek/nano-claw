"""Test configuration loading and validation."""

import os
import pytest
from src.config import Config, LLMConfig, LoggingConfig


class TestConfigDefaults:
    """Test default configuration values."""

    def test_llm_defaults(self, monkeypatch):
        """Test LLM config default values."""
        # Create fresh instance without using singleton (which loads from .env)
        from pydantic_settings import SettingsConfigDict

        # Create instance without .env file loading
        test_config = LLMConfig(_env_file=None)
        assert test_config.provider == "openai"
        assert test_config.model is None
        assert test_config.base_url is None
        assert test_config.api_key is None

    def test_logging_defaults(self, monkeypatch):
        """Test logging config default values."""
        monkeypatch.delenv("ENABLE_LOGGING", raising=False)
        monkeypatch.delenv("ASYNC_LOGGING", raising=False)
        config = Config()
        assert config.logging.enabled is True
        assert config.logging.async_mode is False
        assert config.logging.log_dir == "~/.nano-claw/sessions"
        assert config.logging.buffer_size == 10

    def test_agent_defaults(self, monkeypatch):
        """Test agent config default values."""
        monkeypatch.delenv("AGENT_MAX_ITERATIONS", raising=False)
        config = Config()
        assert config.agent.max_iterations == 10

    def test_ui_defaults(self, monkeypatch):
        """Test UI config default values."""
        monkeypatch.delenv("ENABLE_STREAMING", raising=False)
        config = Config()
        assert config.ui.enable_streaming is True
        assert config.ui.loading_indicator_interval == 0.8
        assert config.ui.live_activity_mode == "simple"
        assert config.ui.live_activity_details == "collapsed"

    def test_subagent_defaults(self, monkeypatch):
        """Test subagent config default values."""
        monkeypatch.delenv("SUBAGENTS_ENABLED", raising=False)
        config = Config()
        assert config.subagents.enabled is True
        assert config.subagents.max_parallel == 3
        assert config.subagents.max_per_turn == 6
        assert config.subagents.default_timeout_seconds == 180

    def test_plan_defaults(self, monkeypatch):
        """Test plan config default values."""
        monkeypatch.delenv("PLAN_ENABLED", raising=False)
        config = Config()
        assert config.plan.enabled is True
        assert config.plan.plan_dir == ".nano-claw/plans"
        assert config.plan.allow_subagents is True


class TestConfigValidation:
    """Test configuration validation."""

    def test_valid_providers(self):
        """Test that all valid providers are accepted."""
        valid_providers = ["openai", "azure", "ollama", "custom"]
        for provider in valid_providers:
            config = LLMConfig(provider=provider)
            assert config.provider == provider

    def test_provider_is_lowercased(self):
        """Test that provider name is normalized to lowercase."""
        config = LLMConfig(provider="OPENAI")
        assert config.provider == "openai"

    def test_buffer_size_validation(self):
        """Test buffer size constraints."""
        # Valid range
        config = LoggingConfig(buffer_size=1)
        assert config.buffer_size == 1

        config = LoggingConfig(buffer_size=100)
        assert config.buffer_size == 100

        # Out of range
        with pytest.raises(ValueError):
            LoggingConfig(buffer_size=0)

        with pytest.raises(ValueError):
            LoggingConfig(buffer_size=101)

    def test_max_iterations_validation(self):
        """Test max_iterations constraints."""
        from src.config import AgentConfig

        # Valid range
        config = AgentConfig(max_iterations=1)
        assert config.max_iterations == 1

        config = AgentConfig(max_iterations=100)
        assert config.max_iterations == 100

        # Out of range
        with pytest.raises(ValueError):
            AgentConfig(max_iterations=0)

        with pytest.raises(ValueError):
            AgentConfig(max_iterations=101)


class TestConfigSingleton:
    """Test Config singleton pattern."""

    def test_config_is_singleton(self):
        """Test that Config.load() returns same instance."""
        c1 = Config.load()
        c2 = Config.load()
        assert c1 is c2

    def test_config_sections_initialized(self):
        """Test that all config sections are initialized."""
        config = Config()
        assert hasattr(config, 'llm')
        assert hasattr(config, 'logging')
        assert hasattr(config, 'agent')
        assert hasattr(config, 'ui')
        assert hasattr(config, 'subagents')
        assert hasattr(config, 'plan')
        assert isinstance(config.llm, LLMConfig)
        assert isinstance(config.logging, LoggingConfig)


class TestConfigEnvOverrides:
    """Test environment variable overrides."""

    def test_provider_env_override(self, monkeypatch):
        """Test LLM_PROVIDER env variable override."""
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        config = Config()
        assert config.llm.provider == "ollama"

    def test_model_env_override(self, monkeypatch):
        """Test LLM_MODEL env variable override."""
        # Set both LLM_MODEL and LLM_PROVIDER for validation
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-3.5-turbo")
        config = Config()
        assert config.llm.model == "gpt-3.5-turbo"

    def test_base_url_env_override(self, monkeypatch):
        """Test LLM_BASE_URL env variable override."""
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        config = Config()
        assert config.llm.base_url == "http://localhost:11434/v1"

    def test_enable_streaming_env_override(self, monkeypatch):
        """Test ENABLE_STREAMING env variable override."""
        monkeypatch.setenv("ENABLE_STREAMING", "false")
        config = Config()
        assert config.ui.enable_streaming is False

    def test_live_activity_env_overrides(self, monkeypatch):
        """Test live activity defaults can be overridden via environment."""
        monkeypatch.setenv("LIVE_ACTIVITY_MODE", "verbose")
        monkeypatch.setenv("LIVE_ACTIVITY_DETAILS", "expanded")
        config = Config()
        assert config.ui.live_activity_mode == "verbose"
        assert config.ui.live_activity_details == "expanded"

    def test_enable_logging_env_override(self, monkeypatch):
        """Test ENABLE_LOGGING env variable override."""
        monkeypatch.setenv("ENABLE_LOGGING", "false")
        config = Config()
        assert config.logging.enabled is False

    def test_async_logging_env_override(self, monkeypatch):
        """Test ASYNC_LOGGING env variable override."""
        monkeypatch.setenv("ASYNC_LOGGING", "true")
        config = Config()
        assert config.logging.async_mode is True

    def test_max_iterations_env_override(self, monkeypatch):
        """Test AGENT_MAX_ITERATIONS env variable override."""
        monkeypatch.setenv("AGENT_MAX_ITERATIONS", "20")
        config = Config()
        assert config.agent.max_iterations == 20

    def test_max_parallel_env_override(self, monkeypatch):
        """Test SUBAGENTS_MAX_PARALLEL env variable override."""
        monkeypatch.setenv("SUBAGENTS_MAX_PARALLEL", "4")
        config = Config()
        assert config.subagents.max_parallel == 4
