"""Tests for runtime extension discovery, execution, and refresh."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import zipfile

import httpx

from src.config import Config
from src.context import Context
from src.extensions import ExtensionManager
from src.runtime_refresh import refresh_live_runtime
from src.skills import SkillManager
from src.tools import ToolProfile, build_tool_registry
from src.tools.extension import ExtensionTool


def write_extension_bundle(
    root: Path,
    *,
    name: str = "sample-extension",
    version: str = "1.0.0",
    tool_name: str = "sample_extension_tool",
    runner_body: str | None = None,
    include_skill: bool = True,
) -> Path:
    """Write one minimal extension bundle for tests."""
    bundle_dir = root / name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    runner_path = bundle_dir / "runner.py"
    runner_path.write_text(
        runner_body
        or (
            "import json, sys\n"
            "payload = json.loads(sys.stdin.read())\n"
            "args = payload.get('arguments', {})\n"
            "mode = args.get('mode', 'success')\n"
            "if mode == 'success':\n"
            "    print(json.dumps({'success': True, 'data': {'echo': args.get('value')}}))\n"
            "elif mode == 'error':\n"
            "    print(json.dumps({'success': False, 'error': 'runner error'}))\n"
            "elif mode == 'malformed':\n"
            "    print('not json')\n"
            "elif mode == 'sleep':\n"
            "    import time\n"
            "    time.sleep(2)\n"
            "    print(json.dumps({'success': True, 'data': 'late'}))\n"
            "else:\n"
            "    print(json.dumps({'success': True, 'data': mode}))\n"
        ),
        encoding="utf-8",
    )
    manifest = {
        "name": name,
        "version": version,
        "description": f"{name} description",
        "command": [sys.executable, "runner.py"],
        "tools": [
            {
                "name": tool_name,
                "description": f"{tool_name} description",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            }
        ],
    }
    (bundle_dir / "EXTENSION.yaml").write_text(json.dumps(manifest), encoding="utf-8")
    if include_skill:
        skill_dir = bundle_dir / "skills" / f"{name}-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}-skill\n"
            f"description: {name} skill\n"
            "---\n\n"
            f"Use {tool_name}.\n",
            encoding="utf-8",
        )
    return bundle_dir


def build_extension_config(temp_dir: Path, **overrides) -> Config:
    """Create a deterministic config for extension tests."""
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
    for key, value in overrides.items():
        payload[key] = value
    return Config(payload)


def test_extension_manager_discovers_repo_override_and_skill_roots(temp_dir):
    """Repo-local extensions should override user-global bundles with the same name."""
    repo_root = temp_dir / "repo"
    user_root = temp_dir / "user-extensions"
    write_extension_bundle(user_root, name="shared-extension", version="1.0.0")
    repo_bundle = write_extension_bundle(
        repo_root / ".nano-claw" / "extensions",
        name="shared-extension",
        version="2.0.0",
    )
    runtime_config = build_extension_config(temp_dir)
    manager = ExtensionManager(
        repo_root=repo_root,
        runtime_config=runtime_config,
        user_root=user_root,
    )

    warnings = manager.discover()

    extension = manager.get_extension("shared-extension")
    assert extension is not None
    assert extension.version == "2.0.0"
    assert extension.root_dir == repo_bundle.resolve()
    assert extension.tool_specs[0].name == "sample_extension_tool"
    skill_roots = manager.get_skill_roots()
    assert len(skill_roots) == 1
    assert skill_roots[0].source == "extension"
    assert skill_roots[0].extension_name == "shared-extension"
    assert skill_roots[0].extension_version == "2.0.0"
    assert any("Duplicate extension 'shared-extension'" in warning for warning in warnings)


def test_build_tool_registry_registers_extension_tools_in_supported_profiles(temp_dir):
    """Extension tools should appear everywhere except plan-subagent mode."""
    repo_root = temp_dir / "repo"
    write_extension_bundle(repo_root / ".nano-claw" / "extensions", name="buildable-extension")
    runtime_config = build_extension_config(temp_dir)
    extension_manager = ExtensionManager(repo_root=repo_root, runtime_config=runtime_config)
    extension_manager.discover()
    skill_manager = SkillManager(
        repo_root=repo_root,
        runtime_config=runtime_config,
        extra_roots=extension_manager.get_skill_roots(),
    )
    skill_manager.discover()

    build_registry = build_tool_registry(
        skill_manager=skill_manager,
        extension_manager=extension_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )
    build_subagent_registry = build_tool_registry(
        skill_manager=skill_manager,
        extension_manager=extension_manager,
        tool_profile=ToolProfile.BUILD_SUBAGENT,
        runtime_config=runtime_config,
    )
    plan_main_registry = build_tool_registry(
        skill_manager=skill_manager,
        extension_manager=extension_manager,
        tool_profile=ToolProfile.PLAN_MAIN,
        runtime_config=runtime_config,
    )
    plan_subagent_registry = build_tool_registry(
        skill_manager=skill_manager,
        extension_manager=extension_manager,
        tool_profile=ToolProfile.PLAN_SUBAGENT,
        runtime_config=runtime_config,
    )

    assert "sample_extension_tool" in build_registry.list_tools()
    assert "sample_extension_tool" in build_subagent_registry.list_tools()
    assert "sample_extension_tool" in plan_main_registry.list_tools()
    assert "sample_extension_tool" not in plan_subagent_registry.list_tools()


def test_extension_tool_executes_runner_and_normalizes_failures(temp_dir):
    """Extension tool calls should normalize success, malformed, error, and timeout paths."""
    repo_root = temp_dir / "repo"
    runtime_config = build_extension_config(temp_dir)
    write_extension_bundle(repo_root / ".nano-claw" / "extensions", name="runner-extension")
    manager = ExtensionManager(repo_root=repo_root, runtime_config=runtime_config)
    manager.discover()
    extension = manager.get_extension("runner-extension")
    assert extension is not None
    tool = ExtensionTool(extension, extension.tool_specs[0], timeout_seconds=1)
    context = Context.create(cwd=str(repo_root))

    success = tool.execute(context, mode="success", value="hello")
    malformed = tool.execute(context, mode="malformed")
    failure = tool.execute(context, mode="error")
    timeout = tool.execute(context, mode="sleep")

    assert success.success is True
    assert success.data == {"echo": "hello"}
    assert malformed.success is False
    assert "invalid JSON" in (malformed.error or "")
    assert failure.success is False
    assert failure.error == "runner error"
    assert timeout.success is False
    assert "timed out" in (timeout.error or "")


def test_refresh_live_runtime_adds_extension_tools_and_prunes_missing_skill(temp_dir):
    """Live refresh should activate new extension tools and drop pinned skills that disappeared."""
    repo_root = temp_dir / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    stale_skill_dir = repo_root / ".nano-claw" / "skills" / "legacy"
    stale_skill_dir.mkdir(parents=True, exist_ok=True)
    (stale_skill_dir / "SKILL.md").write_text(
        "---\nname: legacy\ndescription: Legacy skill\n---\n\nUse the old path.\n",
        encoding="utf-8",
    )
    old_config = build_extension_config(temp_dir)
    old_skill_manager = SkillManager(repo_root=repo_root, runtime_config=old_config)
    old_skill_manager.discover()
    old_registry = build_tool_registry(
        skill_manager=old_skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=old_config,
    )
    context = Context.create(cwd=str(repo_root))
    context.activate_skill("legacy")

    class FakeAgent:
        def __init__(self) -> None:
            self.skill_manager = old_skill_manager
            self.subagent_manager = None
            self.runtime_config = old_config
            self.max_iterations = 10
            self.tools = old_registry
            self.tool_runtime = type("Runtime", (), {"subagent_manager": None})()
            self.context_compaction = type("Compaction", (), {"skill_manager": old_skill_manager})()

        def set_tool_registry(self, tools) -> None:
            self.tools = tools

    class FakeInputHelper:
        def __init__(self) -> None:
            self.updated_skills: list[str] = []

        def update_skills(self, skills) -> None:
            self.updated_skills = list(skills)

    fake_agent = FakeAgent()
    input_helper = FakeInputHelper()
    stale_skill_dir.joinpath("SKILL.md").unlink()
    write_extension_bundle(repo_root / ".nano-claw" / "extensions", name="refreshed-extension")
    refreshed_config = build_extension_config(temp_dir)

    bundle, outcome = refresh_live_runtime(
        repo_root=repo_root,
        agent=fake_agent,
        session_context=context,
        current_skill_manager=old_skill_manager,
        current_tool_registry=old_registry,
        current_mcp_manager=None,
        memory_store=type("MemoryStore", (), {"runtime_config": old_config})(),
        tool_profile=ToolProfile.BUILD,
        config_loader=lambda: refreshed_config,
        include_subagent_tool=True,
        input_helper=input_helper,
        refresh_callback=lambda reason: {"reason": reason},
        reason="test refresh",
    )

    assert "sample_extension_tool" not in old_registry.list_tools()
    assert "sample_extension_tool" in outcome.added_tools
    assert "legacy" not in context.get_active_skills()
    assert outcome.pruned_skills == [{"name": "legacy", "reason": "missing"}]
    assert "refreshed-extension-skill" in input_helper.updated_skills
    assert fake_agent.skill_manager is bundle.skill_manager
    assert "refresh_runtime_capabilities" in fake_agent.tools.list_tools()


def test_extension_manager_installs_catalog_package(temp_dir, monkeypatch):
    """Curated installs should verify hash and install into the user extension root."""
    repo_root = temp_dir / "repo"
    source_root = temp_dir / "bundle-source"
    bundle_dir = write_extension_bundle(source_root, name="catalog-extension")
    archive_path = temp_dir / "catalog-extension.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in bundle_dir.rglob("*"):
            archive.write(path, arcname=str(Path("catalog-extension") / path.relative_to(bundle_dir)))
    archive_bytes = archive_path.read_bytes()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    runtime_config = build_extension_config(
        temp_dir,
        extensions={
            "enabled": True,
            "user_root": str(temp_dir / "user-extensions"),
            "repo_root": ".nano-claw/extensions",
            "runner_timeout_seconds": 1,
            "install_timeout_seconds": 5,
            "catalogs": [{"name": "curated", "url": "https://catalog.test/extensions.json"}],
        },
    )
    manager = ExtensionManager(repo_root=repo_root, runtime_config=runtime_config)

    def fake_get(url: str, **_kwargs):
        request = httpx.Request("GET", url)
        if url.endswith("extensions.json"):
            return httpx.Response(
                200,
                request=request,
                json={
                    "packages": [
                        {
                            "name": "catalog-extension",
                            "version": "1.0.0",
                            "description": "Catalog extension",
                            "archive_url": "https://catalog.test/catalog-extension.zip",
                            "sha256": archive_sha256,
                            "bundle_root": "catalog-extension",
                        }
                    ]
                },
            )
        if url.endswith("catalog-extension.zip"):
            return httpx.Response(200, request=request, content=archive_bytes)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("src.extensions.httpx.get", fake_get)

    result = manager.install_from_catalog("curated:catalog-extension")

    assert result.extension.name == "catalog-extension"
    assert result.install_path.exists()
    assert (result.install_path / "EXTENSION.yaml").exists()
    assert manager.get_extension("catalog-extension") is not None
