"""Tests for the skills runtime."""

import json
from pathlib import Path

from src.config import Config
from src.context import Context
from src.skills import SkillManager
from src.tools.skill import LoadSkillTool


def write_skill(skill_dir: Path, frontmatter: str, body: str = "Use the skill.\n") -> Path:
    """Write a skill bundle to disk."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return skill_file


def test_valid_skill_parses_correctly(temp_dir):
    """Valid skills should parse with metadata and body."""
    repo_root = temp_dir / "repo"
    skill_file = write_skill(
        repo_root / ".babyclaw" / "skills" / "pdf",
        "name: pdf\ndescription: Handle PDFs\nmetadata:\n  short-description: PDF workflows",
        "Prefer visual PDF checks.\n",
    )

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    warnings = manager.discover()

    skill = manager.get_skill("pdf")
    assert warnings == []
    assert skill is not None
    assert skill.skill_file == skill_file.resolve()
    assert skill.description == "Handle PDFs"
    assert skill.short_description == "PDF workflows"
    assert skill.body == "Prefer visual PDF checks."
    assert skill.catalog_visible is True


def test_invalid_frontmatter_is_skipped(temp_dir):
    """Malformed frontmatter should skip the skill and emit a warning."""
    repo_root = temp_dir / "repo"
    skill_dir = repo_root / ".babyclaw" / "skills" / "broken"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: [broken\n---\n\nBody", encoding="utf-8")

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    warnings = manager.discover()

    assert manager.list_skills() == []
    assert any("Skipping invalid skill" in warning for warning in warnings)


def test_missing_required_fields_is_skipped(temp_dir):
    """Skills missing required frontmatter fields should be skipped."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".babyclaw" / "skills" / "incomplete",
        "description: Missing name",
    )

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    warnings = manager.discover()

    assert manager.get_skill("incomplete") is None
    assert any("missing required frontmatter field 'name'" in warning for warning in warnings)


def test_resource_inventory_is_recursive(temp_dir):
    """Scripts, references, and assets should be inventoried recursively."""
    repo_root = temp_dir / "repo"
    skill_dir = repo_root / ".babyclaw" / "skills" / "pdf"
    write_skill(skill_dir, "name: pdf\ndescription: Handle PDFs")
    (skill_dir / "scripts" / "nested").mkdir(parents=True, exist_ok=True)
    (skill_dir / "scripts" / "nested" / "rotate.py").write_text("print('hi')", encoding="utf-8")
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")
    (skill_dir / "assets" / "templates").mkdir(parents=True, exist_ok=True)
    (skill_dir / "assets" / "templates" / "report.txt").write_text("template", encoding="utf-8")

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    skill = manager.get_skill("pdf")
    assert skill is not None
    assert skill.scripts == [(skill_dir / "scripts" / "nested" / "rotate.py").resolve()]
    assert skill.references == [(skill_dir / "references" / "guide.md").resolve()]
    assert skill.assets == [(skill_dir / "assets" / "templates" / "report.txt").resolve()]


def test_repo_skill_overrides_user_skill(temp_dir):
    """Repo-local skills should override user-global skills with the same name."""
    repo_root = temp_dir / "repo"
    user_root = temp_dir / "user-skills"
    write_skill(user_root / "shared", "name: shared\ndescription: User version", "User body")
    repo_skill = write_skill(
        repo_root / ".babyclaw" / "skills" / "shared",
        "name: shared\ndescription: Repo version",
        "Repo body",
    )

    manager = SkillManager(repo_root=repo_root, user_root=user_root)
    warnings = manager.discover()

    skill = manager.get_skill("shared")
    assert skill is not None
    assert skill.source == "repo"
    assert skill.skill_file == repo_skill.resolve()
    assert skill.body == "Repo body"
    assert skill.catalog_visible is True
    assert any("Duplicate skill 'shared'" in warning for warning in warnings)


def test_user_global_skills_are_catalog_visible(temp_dir):
    """User-global skills should be included in the catalog."""
    user_root = temp_dir / "user-skills"
    write_skill(
        user_root / "terraform",
        "name: terraform\ndescription: Handle Terraform\nmetadata:\n  short-description: Terraform workflows",
        "Use terraform plan first.",
    )

    manager = SkillManager(repo_root=temp_dir / "repo", user_root=user_root)
    manager.discover()

    skill = manager.get_skill("terraform")
    assert skill is not None
    assert skill.catalog_visible is True
    assert [catalog_skill.name for catalog_skill in manager.list_catalog_skills()] == ["terraform"]


def test_repo_web_research_skill_is_discoverable_and_catalog_visible(temp_dir):
    """The repo-local web research skill should be available by default."""
    repo_root = Path(__file__).resolve().parent.parent
    manager = SkillManager(
        repo_root=repo_root,
        user_root=temp_dir / "user-skills",
        runtime_config=Config({}),
        platform_name="linux",
    )

    manager.discover()

    skill = manager.get_skill("web-research")
    assert skill is not None
    assert skill.eligible is True
    assert skill.catalog_visible is True
    assert "fetch_url" in skill.body
    assert "MCP search provider" in skill.body
    assert "web-research" in [catalog_skill.name for catalog_skill in manager.list_catalog_skills()]


def test_load_skill_tool_returns_formatted_payload(temp_dir):
    """load_skill should return the skill body and absolute resource paths."""
    repo_root = temp_dir / "repo"
    skill_dir = repo_root / ".babyclaw" / "skills" / "pdf"
    write_skill(skill_dir, "name: pdf\ndescription: Handle PDFs", "Prefer visual checks.")
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    ref_file = skill_dir / "references" / "guide.md"
    ref_file.write_text("guide", encoding="utf-8")

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()
    tool = LoadSkillTool(manager)

    result = tool.execute(Context.create(cwd=str(repo_root)), skill_name="pdf")

    assert result.success is True
    assert "Skill: pdf" in result.data
    assert "Description: Handle PDFs" in result.data
    assert f"Source: {(skill_dir / 'SKILL.md').resolve()}" in result.data
    assert "Instructions:\nPrefer visual checks." in result.data
    assert str(ref_file.resolve()) in result.data


def test_load_skill_tool_returns_error_for_unknown_skill(temp_dir):
    """Unknown skills should return a tool error instead of raising."""
    manager = SkillManager(repo_root=temp_dir / "repo", user_root=temp_dir / "user-skills")
    manager.discover()
    tool = LoadSkillTool(manager)

    result = tool.execute(Context.create(cwd=str(temp_dir)), skill_name="missing")

    assert result.success is False
    assert result.error == "Unknown skill: missing"
    assert result.meta["capability_hint"]["kind"] == "skill"
    assert result.meta["capability_hint"]["name"] == "missing"


def test_list_catalog_skills_returns_repo_and_user_visible_skills(temp_dir):
    """The catalog should contain both repo-local and user-global skills."""
    repo_root = temp_dir / "repo"
    user_root = temp_dir / "user-skills"
    write_skill(repo_root / ".babyclaw" / "skills" / "pdf", "name: pdf\ndescription: Handle PDFs")
    write_skill(user_root / "terraform", "name: terraform\ndescription: Handle Terraform")

    manager = SkillManager(repo_root=repo_root, user_root=user_root)
    manager.discover()

    assert [skill.name for skill in manager.list_catalog_skills()] == ["pdf", "terraform"]


def test_extract_skill_mentions_parses_known_names_and_ignores_unknown(temp_dir):
    """Known $skill mentions should be extracted and deduplicated in order."""
    repo_root = temp_dir / "repo"
    write_skill(repo_root / ".babyclaw" / "skills" / "pdf", "name: pdf\ndescription: Handle PDFs")
    write_skill(repo_root / ".babyclaw" / "skills" / "terraform", "name: terraform\ndescription: Handle Terraform")

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    result = manager.extract_skill_mentions("$pdf summarize with $terraform and $pdf but leave $HOME")

    assert result.skill_names == ["pdf", "terraform"]
    assert result.cleaned_text == "summarize with and but leave $HOME"


def test_build_preload_messages_returns_assistant_tool_pairs(temp_dir):
    """Synthetic preload messages should match assistant/tool transcript structure."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".babyclaw" / "skills" / "pdf",
        "name: pdf\ndescription: Handle PDFs",
        "Prefer visual checks.",
    )
    write_skill(
        repo_root / ".babyclaw" / "skills" / "terraform",
        "name: terraform\ndescription: Handle Terraform",
        "Run plan first.",
    )

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    messages = manager.build_preload_messages(["pdf", "terraform"])

    assert len(messages) == 4
    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "load_skill"
    assert json.loads(messages[0]["tool_calls"][0]["function"]["arguments"]) == {"skill_name": "pdf"}
    assert messages[1]["role"] == "tool"
    assert "Skill: pdf" in messages[1]["content"]
    assert messages[2]["tool_calls"][0]["function"]["name"] == "load_skill"
    assert json.loads(messages[2]["tool_calls"][0]["function"]["arguments"]) == {"skill_name": "terraform"}
    assert messages[3]["role"] == "tool"
    assert "Skill: terraform" in messages[3]["content"]


def test_gated_skill_is_ineligible_when_runtime_requirements_are_missing(temp_dir):
    """macOS-gated skills should stay discoverable but hidden from the active catalog."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".babyclaw" / "skills" / "macos-finder",
        (
            "name: macos-finder\n"
            "description: Finder helper\n"
            "metadata:\n"
            "  short-description: Finder helper\n"
            "  requires:\n"
            "    os:\n"
            "      - darwin\n"
            "    config:\n"
            "      - macos_tools.enabled\n"
            "      - macos_tools.enable_finder"
        ),
        "Use finder_action.\n",
    )
    runtime_config = Config({"macos_tools": {"enabled": False, "enable_finder": True}})
    manager = SkillManager(
        repo_root=repo_root,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )

    manager.discover()

    skill = manager.get_skill("macos-finder")
    assert skill is not None
    assert skill.eligible is False
    assert skill.catalog_visible is False
    assert skill.eligibility_reason == "Requires config: macos_tools.enabled"
    assert manager.list_catalog_skills() == []


def test_gated_skill_is_eligible_by_default_on_darwin(temp_dir, monkeypatch):
    """macOS-gated skills should be eligible by default on Darwin."""
    monkeypatch.delenv("MACOS_TOOLS_ENABLED", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_FINDER", raising=False)
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".babyclaw" / "skills" / "macos-finder",
        (
            "name: macos-finder\n"
            "description: Finder helper\n"
            "metadata:\n"
            "  short-description: Finder helper\n"
            "  requires:\n"
            "    os:\n"
            "      - darwin\n"
            "    config:\n"
            "      - macos_tools.enabled\n"
            "      - macos_tools.enable_finder"
        ),
        "Use finder_action.\n",
    )
    runtime_config = Config({})
    manager = SkillManager(
        repo_root=repo_root,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )

    manager.discover()

    skill = manager.get_skill("macos-finder")
    assert skill is not None
    assert skill.eligible is True
    assert skill.catalog_visible is True
    assert skill.eligibility_reason is None
    assert [item.name for item in manager.list_catalog_skills()] == ["macos-finder"]


def test_load_skill_tool_returns_error_for_ineligible_skill(temp_dir):
    """load_skill should fail cleanly for skills gated off by runtime requirements."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".babyclaw" / "skills" / "macos-notes",
        (
            "name: macos-notes\n"
            "description: Notes helper\n"
            "metadata:\n"
            "  requires:\n"
            "    os:\n"
            "      - darwin\n"
            "    config:\n"
            "      - macos_tools.enabled\n"
            "      - macos_tools.enable_notes"
        ),
        "Use notes_action.\n",
    )
    runtime_config = Config({"macos_tools": {"enabled": True, "enable_notes": False}})
    manager = SkillManager(
        repo_root=repo_root,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )
    manager.discover()
    tool = LoadSkillTool(manager)

    result = tool.execute(Context.create(cwd=str(repo_root)), skill_name="macos-notes")

    assert result.success is False
    assert result.error == "Requires config: macos_tools.enable_notes"
    assert result.meta["capability_hint"]["kind"] == "skill"
    assert result.meta["capability_hint"]["name"] == "macos-notes"


def test_messages_skill_is_eligible_by_default_on_darwin(temp_dir, monkeypatch):
    """Messages-gated skills should be eligible by default on Darwin."""
    monkeypatch.delenv("MACOS_TOOLS_ENABLED", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_MESSAGES", raising=False)
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".babyclaw" / "skills" / "macos-messages",
        (
            "name: macos-messages\n"
            "description: Messages helper\n"
            "metadata:\n"
            "  requires:\n"
            "    os:\n"
            "      - darwin\n"
            "    config:\n"
            "      - macos_tools.enabled\n"
            "      - macos_tools.enable_messages"
        ),
        "Use messages_action.\n",
    )
    runtime_config = Config({})
    manager = SkillManager(
        repo_root=repo_root,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )

    manager.discover()

    skill = manager.get_skill("macos-messages")
    assert skill is not None
    assert skill.eligible is True
    assert skill.catalog_visible is True
    assert skill.eligibility_reason is None


def test_reminders_skill_is_ineligible_when_app_flag_is_disabled(temp_dir):
    """Per-app macOS skill gates should hide only the disabled app skill."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".babyclaw" / "skills" / "macos-reminders",
        (
            "name: macos-reminders\n"
            "description: Reminders helper\n"
            "metadata:\n"
            "  requires:\n"
            "    os:\n"
            "      - darwin\n"
            "    config:\n"
            "      - macos_tools.enabled\n"
            "      - macos_tools.enable_reminders"
        ),
        "Use reminders_action.\n",
    )
    runtime_config = Config({"macos_tools": {"enabled": True, "enable_reminders": False}})
    manager = SkillManager(
        repo_root=repo_root,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )

    manager.discover()

    skill = manager.get_skill("macos-reminders")
    assert skill is not None
    assert skill.eligible is False
    assert skill.catalog_visible is False
    assert skill.eligibility_reason == "Requires config: macos_tools.enable_reminders"


def test_ineligible_skills_do_not_preload_from_explicit_mentions(temp_dir):
    """Explicit $skill mentions should ignore skills that are ineligible in this runtime."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".babyclaw" / "skills" / "macos-calendar",
        (
            "name: macos-calendar\n"
            "description: Calendar helper\n"
            "metadata:\n"
            "  requires:\n"
            "    os:\n"
            "      - darwin\n"
            "    config:\n"
            "      - macos_tools.enabled\n"
            "      - macos_tools.enable_calendar"
        ),
        "Use calendar_action.\n",
    )
    runtime_config = Config({"macos_tools": {"enabled": False, "enable_calendar": True}})
    manager = SkillManager(
        repo_root=repo_root,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )
    manager.discover()

    result = manager.extract_skill_mentions("$macos-calendar show my schedule")

    assert result.skill_names == []
    assert result.cleaned_text == "$macos-calendar show my schedule"
