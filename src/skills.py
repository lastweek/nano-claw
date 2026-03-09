"""Skill discovery and loading for babyclaw."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

import yaml


SkillSource = Literal["repo", "user", "extension"]

MAX_SKILL_BODY_LINES = 500


class SkillUnavailableError(Exception):
    """Raised when a discovered skill is unavailable in the current runtime."""

    def __init__(self, skill_name: str, reason: str) -> None:
        self.skill_name = skill_name
        self.reason = reason
        super().__init__(f"Skill '{skill_name}' is not available: {reason}")


@dataclass
class SkillSpec:
    """A discovered skill bundle."""

    name: str
    description: str
    short_description: str
    body: str
    root_dir: Path
    skill_file: Path
    source: SkillSource
    catalog_visible: bool
    scripts: List[Path]
    references: List[Path]
    assets: List[Path]
    eligible: bool = True
    eligibility_reason: Optional[str] = None
    required_os: List[str] = field(default_factory=list)
    required_config: List[str] = field(default_factory=list)
    extension_name: Optional[str] = None
    extension_version: Optional[str] = None
    extension_install_scope: Optional[str] = None

    @property
    def body_line_count(self) -> int:
        """Return the body line count for context budgeting."""
        return len(self.body.splitlines())

    @property
    def is_oversized(self) -> bool:
        """Return whether the body exceeds the recommended line budget."""
        return self.body_line_count > MAX_SKILL_BODY_LINES


@dataclass(frozen=True)
class SkillDiscoveryRoot:
    """One concrete filesystem root to scan for skills."""

    source: SkillSource
    root: Path
    extension_name: Optional[str] = None
    extension_version: Optional[str] = None
    extension_install_scope: Optional[str] = None


class SkillManager:
    """Discover, inspect, and format Codex-style skill bundles."""

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        user_root: Optional[Path] = None,
        runtime_config: Any | None = None,
        platform_name: str | None = None,
        extra_roots: Optional[List[SkillDiscoveryRoot]] = None,
    ) -> None:
        self.repo_root = (repo_root or Path.cwd()).resolve()
        self.user_root = (user_root or Path.home() / ".babyclaw" / "skills").expanduser().resolve()
        self.repo_skills_root = self.repo_root / ".babyclaw" / "skills"
        self.runtime_config = runtime_config
        self.platform_name = (platform_name or sys.platform).lower()
        self.extra_roots = list(extra_roots or [])
        self._skills: Dict[str, SkillSpec] = {}
        self._warnings: List[str] = []

    def discover(self) -> List[str]:
        """Discover skills under the configured roots and return warnings."""
        skills: Dict[str, SkillSpec] = {}
        warnings: List[str] = []

        discovery_roots: List[SkillDiscoveryRoot] = [
            SkillDiscoveryRoot(source="user", root=self.user_root),
            *self.extra_roots,
            SkillDiscoveryRoot(source="repo", root=self.repo_skills_root),
        ]

        for discovery_root in discovery_roots:
            root = discovery_root.root
            if not root.exists():
                continue

            for skill_file in sorted(root.rglob("SKILL.md")):
                spec, skill_warnings = self._load_skill_file(skill_file, discovery_root)
                warnings.extend(skill_warnings)
                if spec is None:
                    continue

                previous = skills.get(spec.name)
                if previous is not None:
                    warnings.append(
                        f"Duplicate skill '{spec.name}': {spec.skill_file} overrides {previous.skill_file}"
                    )
                skills[spec.name] = spec

        self._skills = skills
        self._warnings = warnings
        return list(warnings)

    def list_skills(self) -> List[SkillSpec]:
        """Return discovered skills."""
        return sorted(self._skills.values(), key=lambda skill: skill.name)

    def list_catalog_skills(self) -> List[SkillSpec]:
        """Return the predefined repo-local skills shown in the system prompt."""
        return [
            skill
            for skill in self.list_skills()
            if skill.catalog_visible and skill.eligible
        ]

    def get_skill(self, name: str) -> Optional[SkillSpec]:
        """Return a discovered skill by name."""
        return self._skills.get(name)

    def get_warnings(self) -> List[str]:
        """Return warnings from the last discovery run."""
        return list(self._warnings)

    def extract_skill_mentions(self, text: str) -> "SkillMentionParseResult":
        """Parse explicit $skill-name mentions from user text."""
        found: List[str] = []

        def replacer(match: re.Match) -> str:
            skill_name = match.group(1)
            skill = self._skills.get(skill_name)
            if skill is None or not skill.eligible:
                return match.group(0)

            if skill_name not in found:
                found.append(skill_name)
            return ""

        cleaned = re.sub(r"\$([A-Za-z0-9][A-Za-z0-9_-]*)\b", replacer, text)
        cleaned = " ".join(cleaned.split())
        return SkillMentionParseResult(skill_names=found, cleaned_text=cleaned)

    def build_preload_messages(self, skill_names: List[str]) -> List[dict]:
        """Build deterministic synthetic assistant/tool messages for skill preloads."""
        messages: List[dict] = []

        for index, skill_name in enumerate(skill_names, start=1):
            skill = self.get_skill(skill_name)
            if skill is None or not skill.eligible:
                continue

            tool_call_id = f"skill_preload_{index}_{skill.name}"
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": "load_skill",
                            "arguments": json.dumps({"skill_name": skill.name}),
                        },
                    }
                ],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps({"output": self._format_skill_payload(skill)}),
            })

        return messages

    def format_skill_for_tool(self, name: str) -> str:
        """Format a skill payload for the agent tool result."""
        skill = self.get_skill(name)
        if skill is None:
            raise KeyError(name)
        if not skill.eligible:
            raise SkillUnavailableError(name, skill.eligibility_reason or "Skill is not eligible")
        return self._format_skill_payload(skill)

    def _load_skill_file(
        self,
        skill_file: Path,
        discovery_root: SkillDiscoveryRoot,
    ) -> tuple[Optional[SkillSpec], List[str]]:
        warnings: List[str] = []

        try:
            raw_text = skill_file.read_text(encoding="utf-8")
        except Exception as exc:
            return None, [f"Failed to read skill file {skill_file}: {exc}"]

        try:
            metadata, body = self._parse_skill_markdown(raw_text)
        except ValueError as exc:
            return None, [f"Skipping invalid skill {skill_file}: {exc}"]

        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            return None, [f"Skipping skill {skill_file}: missing required frontmatter field 'name'"]
        if not isinstance(description, str) or not description.strip():
            return None, [f"Skipping skill {skill_file}: missing required frontmatter field 'description'"]

        metadata_block = metadata.get("metadata", {})
        short_description = description.strip()
        required_os: List[str] = []
        required_config: List[str] = []
        if isinstance(metadata_block, dict):
            short_candidate = metadata_block.get("short-description")
            if isinstance(short_candidate, str) and short_candidate.strip():
                short_description = short_candidate.strip()
            requires_block = metadata_block.get("requires", {})
            if isinstance(requires_block, dict):
                required_os = self._normalize_string_list(requires_block.get("os"))
                required_config = self._normalize_string_list(requires_block.get("config"))

        eligible, eligibility_reason = self._evaluate_skill_eligibility(
            required_os=required_os,
            required_config=required_config,
        )

        root_dir = skill_file.parent.resolve()
        skill = SkillSpec(
            name=name.strip(),
            description=description.strip(),
            short_description=short_description,
            body=body.strip(),
            root_dir=root_dir,
            skill_file=skill_file.resolve(),
            source=discovery_root.source,
            catalog_visible=eligible,
            scripts=self._inventory_resources(root_dir / "scripts"),
            references=self._inventory_resources(root_dir / "references"),
            assets=self._inventory_resources(root_dir / "assets"),
            eligible=eligible,
            eligibility_reason=eligibility_reason,
            required_os=required_os,
            required_config=required_config,
            extension_name=discovery_root.extension_name,
            extension_version=discovery_root.extension_version,
            extension_install_scope=discovery_root.extension_install_scope,
        )

        if skill.is_oversized:
            warnings.append(
                f"Skill '{skill.name}' has {skill.body_line_count} body lines; consider moving detail into references/"
            )

        return skill, warnings

    def _parse_skill_markdown(self, text: str) -> tuple[dict, str]:
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise ValueError("missing YAML frontmatter")

        end_index = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                end_index = index
                break

        if end_index is None:
            raise ValueError("unterminated YAML frontmatter")

        frontmatter = "".join(lines[1:end_index])
        body = "".join(lines[end_index + 1 :])

        try:
            metadata = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML frontmatter: {exc}") from exc

        if not isinstance(metadata, dict):
            raise ValueError("frontmatter must parse to a mapping")

        return metadata, body

    def _inventory_resources(self, root: Path) -> List[Path]:
        if not root.exists():
            return []
        return sorted(path.resolve() for path in root.rglob("*") if path.is_file())

    def _normalize_string_list(self, value: Any) -> List[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            normalized = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    normalized.append(item.strip())
            return normalized
        return []

    def _evaluate_skill_eligibility(
        self,
        *,
        required_os: List[str],
        required_config: List[str],
    ) -> tuple[bool, str | None]:
        normalized_required_os = [entry.lower() for entry in required_os if entry]
        if normalized_required_os and self.platform_name not in normalized_required_os:
            return False, f"Requires os: {', '.join(required_os)}"

        for config_path in required_config:
            value = self._get_config_value(config_path)
            if not bool(value):
                return False, f"Requires config: {config_path}"

        return True, None

    def _get_config_value(self, dotted_path: str) -> Any:
        config_root = self.runtime_config
        if config_root is None:
            from src.config import config as runtime_config

            config_root = runtime_config

        current = config_root
        for segment in dotted_path.split("."):
            if isinstance(current, dict):
                current = current.get(segment)
                continue
            current = getattr(current, segment, None)
            if current is None:
                return None
        return current

    def _format_skill_payload(self, skill: SkillSpec) -> str:
        sections = [
            f"Skill: {skill.name}",
            f"Description: {skill.description}",
            f"Source: {skill.skill_file}",
            f"Catalog Source: {skill.source}",
            f"Extension: {skill.extension_name or 'n/a'}",
            f"Extension Version: {skill.extension_version or 'n/a'}",
            f"Eligible: {'yes' if skill.eligible else 'no'}",
            f"Eligibility Reason: {skill.eligibility_reason or 'n/a'}",
            "",
            "Instructions:",
            skill.body,
            "",
            "Bundled resources:",
            *self._resource_group("Scripts", skill.scripts),
            *self._resource_group("References", skill.references),
            *self._resource_group("Assets", skill.assets),
            "",
            "Use read_file to inspect resource files as needed. Do not assume bundled scripts have already been executed.",
        ]
        return "\n".join(sections).strip()

    def _resource_group(self, label: str, paths: Iterable[Path]) -> List[str]:
        lines = [f"{label}:"]
        entries = [f"- {path}" for path in paths]
        if entries:
            lines.extend(entries)
        else:
            lines.append("- none")
        return lines

@dataclass
class SkillMentionParseResult:
    """Parsed explicit skill mentions from a user message."""

    skill_names: List[str]
    cleaned_text: str
