"""Tests for capability discovery and missing-capability requests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx

from src.capabilities import CapabilityInventory, CapabilityRequestManager
from src.config import Config
from src.extensions import ExtensionManager
from src.skills import SkillManager
from src.tools import Tool, ToolProfile, ToolRegistry, build_tool_registry_with_report


class ActiveTool(Tool):
    """Simple tool used to verify active capability search."""

    name = "active_tool"
    description = "Handle the active capability."
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    def execute(self, context, **kwargs):
        raise NotImplementedError


def build_capability_config(temp_dir: Path, **overrides) -> Config:
    """Create a deterministic runtime config for capability tests."""
    payload = {
        "extensions": {
            "enabled": True,
            "user_root": str(temp_dir / "user-extensions"),
            "repo_root": ".nano-claw/extensions",
            "runner_timeout_seconds": 1,
            "install_timeout_seconds": 5,
            "catalogs": [],
        },
        "mcp": {"servers": []},
        "memory": {"enabled": False},
        "web_tools": {
            "enabled": False,
            "enable_fetch_url": False,
            "enable_read_webpage": False,
            "enable_extract_page_links": False,
        },
        "macos_tools": {"enabled": False},
        "subagents": {"enabled": False},
    }
    payload.update(overrides)
    return Config(payload)


def write_skill(
    skill_dir: Path,
    *,
    name: str,
    description: str,
    body: str,
    extra_frontmatter: str = "",
) -> None:
    """Write one skill bundle."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{extra_frontmatter}"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def write_extension_bundle(
    root: Path,
    *,
    name: str = "sample-extension",
    tool_name: str = "sample_extension_tool",
) -> Path:
    """Write one minimal extension bundle with a bundled skill."""
    bundle_dir = root / name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "runner.py").write_text(
        "import json, sys\n"
        "print(json.dumps({'success': True, 'data': json.loads(sys.stdin.read())}))\n",
        encoding="utf-8",
    )
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": f"{name} description",
        "command": [sys.executable, "runner.py"],
        "tools": [
            {
                "name": tool_name,
                "description": f"{tool_name} description",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }
        ],
    }
    (bundle_dir / "EXTENSION.yaml").write_text(json.dumps(manifest), encoding="utf-8")
    write_skill(
        bundle_dir / "skills" / f"{name}-skill",
        name=f"{name}-skill",
        description=f"{name} skill",
        body=f"Use {tool_name}.",
    )
    return bundle_dir


def test_capability_request_manager_dedup_manual_status_and_auto_resolve():
    """Repeated requests should dedupe, allow manual status changes, and auto-resolve exact targets."""
    manager = CapabilityRequestManager()

    first = manager.create_or_update(
        summary="Need GitHub tools",
        reason="The task requires GitHub API access.",
        desired_capability="github issue tools",
        request_type="install_extension",
        package_ref="curated:github",
        extension_name="github",
    )
    second = manager.create_or_update(
        summary="Need GitHub tools",
        reason="Still blocked without GitHub access.",
        desired_capability="github issue tools",
        request_type="install_extension",
        package_ref="curated:github",
        extension_name="github",
    )
    generic = manager.create_or_update(
        summary="Need browser automation",
        reason="The site is JS-heavy.",
        desired_capability="playwright browser automation",
        request_type="generic",
    )

    assert second.request_id == first.request_id
    assert second.occurrence_count == 2
    assert manager.pending_count() == 2

    resolved_ids = manager.auto_resolve(
        tool_registry=ToolRegistry(),
        skill_manager=None,
        extension_manager=SimpleNamespace(
            get_extension=lambda name: SimpleNamespace(name=name) if name == "github" else None
        ),
    )

    assert resolved_ids == [first.request_id]
    assert manager.get_request(first.request_id).status == "resolved"
    assert manager.get_request(generic.request_id).status == "pending"

    dismissed = manager.dismiss_request(generic.request_id)
    assert dismissed.status == "dismissed"
    reopened = manager.create_or_update(
        summary="Need browser automation",
        reason="Still blocked on JS rendering.",
        desired_capability="playwright browser automation",
        request_type="generic",
    )
    assert reopened.request_id != generic.request_id
    resolved = manager.resolve_request(reopened.request_id)
    assert resolved.status == "resolved"


def test_capability_inventory_search_matches_active_and_skill_states(temp_dir):
    """Capability search should surface active tools plus eligible and ineligible skills."""
    repo_root = temp_dir / "repo"
    runtime_config = build_capability_config(temp_dir)
    write_skill(
        repo_root / ".nano-claw" / "skills" / "terraform",
        name="terraform",
        description="Terraform workflows",
        body="Use terraform plan first.",
    )
    write_skill(
        repo_root / ".nano-claw" / "skills" / "macos-finder",
        name="macos-finder",
        description="Finder helper",
        body="Use finder_action.",
        extra_frontmatter=(
            "metadata:\n"
            "  requires:\n"
            "    os:\n"
            "      - darwin\n"
            "    config:\n"
            "      - macos_tools.enabled\n"
        ),
    )
    skill_manager = SkillManager(repo_root=repo_root, runtime_config=runtime_config, platform_name="linux")
    skill_manager.discover()
    tool_registry = ToolRegistry()
    tool_registry.register(ActiveTool())

    inventory = CapabilityInventory(repo_root=repo_root, runtime_config=runtime_config)
    inventory.bind_runtime(
        tool_registry=tool_registry,
        skill_manager=skill_manager,
        extension_manager=None,
    )

    active_match = inventory.search("active_tool", limit=5)[0]
    eligible_skill = inventory.search("terraform", limit=5)[0]
    ineligible_skill = inventory.search("macos-finder", limit=5)[0]

    assert active_match["kind"] == "tool"
    assert active_match["availability"] == "active"
    assert eligible_skill["kind"] == "skill"
    assert eligible_skill["availability"] == "loadable"
    assert ineligible_skill["availability"] == "ineligible"
    assert "darwin" in (ineligible_skill["reason_unavailable"] or "")


def test_capability_inventory_search_matches_reload_required_and_catalog_package(temp_dir, monkeypatch):
    """Capability search should find on-disk bundles that need reload and exact catalog packages."""
    repo_root = temp_dir / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    runtime_config = build_capability_config(
        temp_dir,
        extensions={
            "enabled": True,
            "user_root": str(temp_dir / "user-extensions"),
            "repo_root": ".nano-claw/extensions",
            "runner_timeout_seconds": 1,
            "install_timeout_seconds": 5,
            "catalogs": [{"name": "curated", "url": "https://catalog.example/extensions.json"}],
        },
    )
    skill_manager = SkillManager(repo_root=repo_root, runtime_config=runtime_config)
    skill_manager.discover()
    inventory = CapabilityInventory(repo_root=repo_root, runtime_config=runtime_config)

    write_extension_bundle(repo_root / ".nano-claw" / "extensions", name="browser-tools", tool_name="browser_open")

    class FakeResponse:
        text = json.dumps(
            {
                "packages": [
                    {
                        "name": "github",
                        "version": "1.0.0",
                        "archive_url": "https://example.invalid/github.zip",
                        "sha256": "abc123",
                        "bundle_root": "github",
                        "description": "GitHub automation tools",
                    }
                ]
            }
        )

        def raise_for_status(self) -> None:
            return

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: FakeResponse())
    live_extension_manager = ExtensionManager(
        repo_root=repo_root,
        runtime_config=runtime_config,
    )
    inventory.bind_runtime(
        tool_registry=ToolRegistry(),
        skill_manager=skill_manager,
        extension_manager=live_extension_manager,
    )

    reload_required = inventory.search("browser_open", limit=5)[0]
    installable = inventory.search("github", limit=5)[0]

    assert reload_required["availability"] == "reload_required"
    assert reload_required["extension_name"] == "browser-tools"
    assert "/runtime reload" in reload_required["suggested_cli_actions"]
    assert installable["availability"] == "installable"
    assert installable["package_ref"] == "curated:github"
    assert installable["suggested_cli_actions"] == [
        "/extension install curated:github",
        "/runtime reload",
    ]


def test_build_tool_registry_registers_capability_tools_only_in_main_profiles(temp_dir):
    """Capability tools should be present only for the main build and planning profiles."""
    repo_root = temp_dir / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    runtime_config = build_capability_config(temp_dir)
    skill_manager = SkillManager(repo_root=repo_root, runtime_config=runtime_config)
    skill_manager.discover()
    capability_inventory = CapabilityInventory(repo_root=repo_root, runtime_config=runtime_config)
    capability_request_manager = CapabilityRequestManager()

    build_registry, build_report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        capability_inventory=capability_inventory,
        capability_request_manager=capability_request_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )
    plan_main_registry, _ = build_tool_registry_with_report(
        skill_manager=skill_manager,
        capability_inventory=capability_inventory,
        capability_request_manager=capability_request_manager,
        tool_profile=ToolProfile.PLAN_MAIN,
        runtime_config=runtime_config,
    )
    build_subagent_registry, build_subagent_report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        capability_inventory=capability_inventory,
        capability_request_manager=capability_request_manager,
        tool_profile=ToolProfile.BUILD_SUBAGENT,
        runtime_config=runtime_config,
    )
    plan_subagent_registry, plan_subagent_report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        capability_inventory=capability_inventory,
        capability_request_manager=capability_request_manager,
        tool_profile=ToolProfile.PLAN_SUBAGENT,
        runtime_config=runtime_config,
    )

    assert "find_capabilities" in build_registry.list_tools()
    assert "request_capability" in build_registry.list_tools()
    assert "find_capabilities" in plan_main_registry.list_tools()
    assert "request_capability" in plan_main_registry.list_tools()
    assert "find_capabilities" not in build_subagent_registry.list_tools()
    assert "request_capability" not in build_subagent_registry.list_tools()
    assert "find_capabilities" not in plan_subagent_registry.list_tools()
    assert "request_capability" not in plan_subagent_registry.list_tools()
    assert any(
        decision.name == "capability_tools" and decision.status == "registered"
        for decision in build_report.group_decisions
    )
    assert any(
        decision.name == "capability_tools"
        and "requires build or plan_main" in decision.status
        for decision in build_subagent_report.group_decisions
    )
    assert any(
        decision.name == "capability_tools"
        and "requires build or plan_main" in decision.status
        for decision in plan_subagent_report.group_decisions
    )
