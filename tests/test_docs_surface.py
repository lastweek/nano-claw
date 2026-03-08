"""Checks that docs and config surfaces stay aligned on key settings."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_config_example_includes_public_subagent_limits():
    """Documented public subagent limits should exist in the example config."""
    example_config = (REPO_ROOT / "config.yaml.example").read_text(encoding="utf-8")

    assert "max_parallel: 3" in example_config
    assert "max_per_turn: 6" in example_config
    assert "macos_tools:" in example_config
    assert "macos_tools:\n  enabled: true" in example_config
    assert "enable_reminders: true" in example_config
    assert "enable_messages: true" in example_config
    assert "web_tools:" in example_config
    assert "web_tools:\n  enabled: true" in example_config
    assert "allow_private_networks: false" in example_config
    assert "extensions:" in example_config
    assert "extensions:\n  enabled: true" in example_config
    assert "runner_timeout_seconds: 60" in example_config


def test_docs_reference_async_logging_and_max_parallel():
    """Public docs should mention the supported logging and subagent settings."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design_overview = (REPO_ROOT / "docs" / "design-overview.md").read_text(encoding="utf-8")
    subagents_doc = (REPO_ROOT / "docs" / "subagents.md").read_text(encoding="utf-8")

    assert "logging.async_mode" in readme
    assert "macos_tools.enabled" in readme
    assert "reminders_action" in readme
    assert "messages_action" in readme
    assert "web_tools.enabled" in readme
    assert "fetch_url" in readme
    assert "read_webpage" in readme
    assert "extract_page_links" in readme
    assert "extensions.enabled" in readme
    assert "/runtime reload" in readme
    assert "/extension install" in readme
    assert "find_capabilities" in readme
    assert "request_capability" in readme
    assert "/capability" in readme
    assert "max_parallel" in design_overview
    assert "max_parallel" in subagents_doc
