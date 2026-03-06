"""Centralized configuration for nano-claw."""

from typing import Optional, List, Literal
from pathlib import Path
import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from src.utils import env_truthy


class LLMConfig(BaseSettings):
    """LLM provider configuration."""

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    provider: str = Field(default="openai", description="LLM provider: openai, azure, ollama, custom")
    base_url: Optional[str] = Field(default=None, description="Custom API base URL")
    model: Optional[str] = Field(default=None, description="Model name")
    api_key: Optional[str] = Field(default=None, description="API key (auto-detected from provider)")
    context_window: Optional[int] = Field(default=None, description="Model context window size (tokens)")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        """Normalize provider name to lowercase."""
        return v.lower()


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    enabled: bool = Field(default=True, alias="ENABLE_LOGGING")
    async_mode: bool = Field(default=False, alias="ASYNC_LOGGING")
    log_dir: str = Field(default="logs", alias="LOG_DIR")
    buffer_size: int = Field(default=10, ge=1, le=100)


class AgentConfig(BaseSettings):
    """Agent behavior configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    max_iterations: int = Field(default=10, ge=1, le=100)


class UIConfig(BaseSettings):
    """UI/Display configuration."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    enable_streaming: bool = Field(default=True)
    loading_indicator_interval: float = Field(default=0.8, gt=0)
    live_activity_mode: Literal["simple", "verbose"] = Field(default="simple")
    live_activity_details: Literal["collapsed", "expanded"] = Field(default="collapsed")


class ContextConfig(BaseSettings):
    """Context compaction configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CONTEXT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    auto_compact: bool = Field(default=True)
    auto_compact_threshold: float = Field(default=0.85)
    target_usage_after_compaction: float = Field(default=0.60)
    min_recent_turns: int = Field(default=6, ge=1)

    @field_validator("auto_compact_threshold")
    @classmethod
    def validate_auto_compact_threshold(cls, value: float) -> float:
        """Ensure the auto-compaction threshold is a sensible ratio."""
        if not 0 < value < 1:
            raise ValueError("auto_compact_threshold must be between 0 and 1")
        return value

    @field_validator("target_usage_after_compaction")
    @classmethod
    def validate_target_usage_after_compaction(cls, value: float) -> float:
        """Ensure the post-compaction target is a sensible ratio."""
        if not 0 < value < 1:
            raise ValueError("target_usage_after_compaction must be between 0 and 1")
        return value


class SubagentConfig(BaseSettings):
    """Local subagent runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="SUBAGENTS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    enabled: bool = Field(default=True)
    max_parallel: int = Field(default=3, ge=1)
    max_per_turn: int = Field(default=6, ge=1)
    default_timeout_seconds: int = Field(default=180, ge=1)


class PlanConfig(BaseSettings):
    """Planning workflow configuration."""

    model_config = SettingsConfigDict(
        env_prefix="PLAN_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=True)
    plan_dir: str = Field(default=".nano-claw/plans")
    allow_subagents: bool = Field(default=True)


class ServerConfig(BaseSettings):
    """Local HTTP server configuration."""

    model_config = SettingsConfigDict(
        env_prefix="SERVER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8765, ge=1, le=65535)
    db_path: str = Field(default=".nano-claw/state.db")
    max_parallel_runs: int = Field(default=1, ge=1)
    serve_ui: bool = Field(default=True)
    sse_heartbeat_seconds: int = Field(default=10, ge=1)


class MCPServerConfig(BaseSettings):
    """Configuration for a single MCP server."""

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow"
    )

    name: str = Field(description="Unique server name")
    url: str = Field(description="MCP server URL")
    enabled: bool = Field(default=True, description="Whether to connect to this server")
    timeout: int = Field(default=30, description="Request timeout in seconds")


class MCPConfig(BaseSettings):
    """MCP servers configuration."""

    model_config = SettingsConfigDict(
        env_prefix="MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="allow"
    )

    servers: List[MCPServerConfig] = Field(
        default_factory=list,
        description="List of MCP servers"
    )


class Config:
    """Global configuration container."""

    _instance: Optional['Config'] = None
    _config_path: Path = Path("config.yaml")
    _last_load_messages: List[str] = []

    def __init__(self, config_dict: Optional[dict] = None):
        """Initialize all config sections.

        Priority (highest to lowest):
        1. Environment variables
        2. config.yaml values
        3. Default values
        """
        config_dict = config_dict or {}

        # Initialize each config section
        # Create instances first (loads env vars via pydantic-settings)
        # Then apply yaml values only for fields that don't have values set
        self.llm = self._create_config(LLMConfig, config_dict.get("llm", {}))
        self.logging = self._create_config(LoggingConfig, config_dict.get("logging", {}))
        self.agent = self._create_config(AgentConfig, config_dict.get("agent", {}))
        self.ui = self._create_config(UIConfig, config_dict.get("ui", {}))
        self.context = self._create_config(ContextConfig, config_dict.get("context", {}))
        self.subagents = self._create_config(SubagentConfig, config_dict.get("subagents", {}))
        self.plan = self._create_config(PlanConfig, config_dict.get("plan", {}))
        self.server = self._create_config(ServerConfig, config_dict.get("server", {}))
        self.mcp = self._create_mcp_config(config_dict.get("mcp", {}))

        if self.context.target_usage_after_compaction >= self.context.auto_compact_threshold:
            raise ValueError(
                "context.target_usage_after_compaction must be less than "
                "context.auto_compact_threshold"
            )

    @staticmethod
    def _create_config(config_class, yaml_values: dict):
        """Create a config section with proper priority: env vars > yaml > defaults.

        Args:
            config_class: Pydantic Settings class to instantiate
            yaml_values: Dict of values from config.yaml

        Returns:
            Config instance with env vars taking precedence over yaml values
        """
        # Import os to check if env var is set
        import os

        # Check each field to see if an env var is set
        # Only apply yaml value if no env var is set
        filtered_values = {}
        for key, value in yaml_values.items():
            # Get the env var name for this field
            # For LLMConfig, env prefix is "LLM_"
            # For LoggingConfig, no prefix, but uses aliases like "ENABLE_LOGGING"
            # For AgentConfig, env prefix is "AGENT_"
            # For UIConfig, no prefix

            env_var_name = None
            if hasattr(config_class, 'model_config'):
                # Check for env_prefix in settings config
                env_prefix = config_class.model_config.get('env_prefix', '')
                # Build env var name: PREFIX + KEY (uppercased)
                env_var_name = (env_prefix + key).upper()

                # For fields with aliases (like ENABLE_LOGGING), check those too
                if key in config_class.model_fields:
                    field = config_class.model_fields[key]
                    if hasattr(field, 'alias'):
                        env_var_name = field.alias

            # Check if env var is set
            if env_var_name and env_var_name in os.environ:
                # Env var is set, skip yaml value (env var takes precedence)
                continue
            elif not env_var_name and key.upper() in os.environ:
                # Try uppercase key without prefix
                continue
            else:
                # No env var set, use yaml value
                filtered_values[key] = value

        # Create instance with filtered yaml values
        # Pydantic-settings will still apply env vars for any fields not in filtered_values
        return config_class(**filtered_values)

    @staticmethod
    def _create_mcp_config(yaml_values: dict) -> MCPConfig:
        """Create MCP config from yaml values.

        Args:
            yaml_values: Dict with 'servers' key containing list of server configs

        Returns:
            MCPConfig instance
        """
        servers_list = yaml_values.get("servers", [])

        # Convert each server dict to MCPServerConfig
        servers = []
        for server_dict in servers_list:
            servers.append(MCPServerConfig(**server_dict))

        return MCPConfig(servers=servers)

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> 'Config':
        """Load or create singleton config instance.

        Priority:
        1. config.yaml (if exists and not in test mode) → provides base values
        2. Environment variables → override yaml and defaults
        3. Class defaults → fallback
        """
        if cls._instance is not None:
            return cls._instance

        # Load from config.yaml if it exists and not in test mode
        config_dict = {}
        load_messages: List[str] = []
        if config_path:
            cls._config_path = Path(config_path)

        # Skip loading config.yaml in test mode (when NANO_CODER_TEST is set)
        is_test_mode = env_truthy("NANO_CODER_TEST")

        if not is_test_mode and cls._config_path.exists():
            try:
                with open(cls._config_path) as f:
                    config_dict = yaml.safe_load(f) or {}
                load_messages.append(f"Loaded configuration from {cls._config_path}")
            except Exception as e:
                load_messages.append(f"Warning: Failed to load config from {cls._config_path}: {e}")
                load_messages.append("Using environment variables and defaults.")
        else:
            if is_test_mode:
                load_messages.append(
                    "Test mode: Skipping config.yaml, using environment variables and defaults."
                )
            else:
                load_messages.append(f"No config.yaml found at {cls._config_path}")
                load_messages.append("Using environment variables and defaults.")

        # Create config instance (env vars will be applied by pydantic-settings)
        cls._instance = cls(config_dict)
        cls._last_load_messages = load_messages

        return cls._instance

    @classmethod
    def reload(cls) -> 'Config':
        """Force reload the configuration.

        This resets the singleton and creates a new instance with current
        environment variables. Useful for testing.

        Returns:
            New Config instance
        """
        cls._instance = None
        new_instance = cls.load()
        # Update global config variable
        global config
        config = new_instance
        return new_instance

    @classmethod
    def get_load_messages(cls) -> List[str]:
        """Return the diagnostic messages captured during the most recent load."""
        return list(cls._last_load_messages)


# Global config instance
config = Config.load()
