"""Tests for the skills runtime."""

import json
from pathlib import Path

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
        repo_root / ".nano-claw" / "skills" / "pdf",
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
    skill_dir = repo_root / ".nano-claw" / "skills" / "broken"
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
        repo_root / ".nano-claw" / "skills" / "incomplete",
        "description: Missing name",
    )

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    warnings = manager.discover()

    assert manager.get_skill("incomplete") is None
    assert any("missing required frontmatter field 'name'" in warning for warning in warnings)


def test_resource_inventory_is_recursive(temp_dir):
    """Scripts, references, and assets should be inventoried recursively."""
    repo_root = temp_dir / "repo"
    skill_dir = repo_root / ".nano-claw" / "skills" / "pdf"
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
        repo_root / ".nano-claw" / "skills" / "shared",
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


def test_load_skill_tool_returns_formatted_payload(temp_dir):
    """load_skill should return the skill body and absolute resource paths."""
    repo_root = temp_dir / "repo"
    skill_dir = repo_root / ".nano-claw" / "skills" / "pdf"
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


def test_list_catalog_skills_returns_repo_and_user_visible_skills(temp_dir):
    """The catalog should contain both repo-local and user-global skills."""
    repo_root = temp_dir / "repo"
    user_root = temp_dir / "user-skills"
    write_skill(repo_root / ".nano-claw" / "skills" / "pdf", "name: pdf\ndescription: Handle PDFs")
    write_skill(user_root / "terraform", "name: terraform\ndescription: Handle Terraform")

    manager = SkillManager(repo_root=repo_root, user_root=user_root)
    manager.discover()

    assert [skill.name for skill in manager.list_catalog_skills()] == ["pdf", "terraform"]


def test_extract_skill_mentions_parses_known_names_and_ignores_unknown(temp_dir):
    """Known $skill mentions should be extracted and deduplicated in order."""
    repo_root = temp_dir / "repo"
    write_skill(repo_root / ".nano-claw" / "skills" / "pdf", "name: pdf\ndescription: Handle PDFs")
    write_skill(repo_root / ".nano-claw" / "skills" / "terraform", "name: terraform\ndescription: Handle Terraform")

    manager = SkillManager(repo_root=repo_root, user_root=temp_dir / "user-skills")
    manager.discover()

    result = manager.extract_skill_mentions("$pdf summarize with $terraform and $pdf but leave $HOME")

    assert result.skill_names == ["pdf", "terraform"]
    assert result.cleaned_text == "summarize with and but leave $HOME"


def test_build_preload_messages_returns_assistant_tool_pairs(temp_dir):
    """Synthetic preload messages should match assistant/tool transcript structure."""
    repo_root = temp_dir / "repo"
    write_skill(
        repo_root / ".nano-claw" / "skills" / "pdf",
        "name: pdf\ndescription: Handle PDFs",
        "Prefer visual checks.",
    )
    write_skill(
        repo_root / ".nano-claw" / "skills" / "terraform",
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
