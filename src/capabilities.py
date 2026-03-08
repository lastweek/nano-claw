"""Capability discovery and missing-capability request tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import re
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from src.extensions import ExtensionManager
from src.skills import SkillManager


CapabilityAvailability = Literal[
    "active",
    "loadable",
    "reload_required",
    "installable",
    "ineligible",
]
CapabilityRequestType = Literal[
    "reload_runtime",
    "install_extension",
    "enable_config",
    "generic",
]
CapabilityRequestStatus = Literal["pending", "dismissed", "resolved"]


def _now_iso() -> str:
    """Return a stable UTC timestamp for runtime-only records."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_capability_text(value: str) -> str:
    """Normalize free-form capability text for dedupe and search."""
    collapsed = " ".join(str(value or "").strip().lower().split())
    return re.sub(r"[^a-z0-9:_./ -]+", "", collapsed)


def build_capability_hint(
    *,
    query: str,
    message: str,
    kind: str,
    name: str | None = None,
) -> dict[str, Any]:
    """Build a consistent recovery hint for missing capabilities."""
    return {
        "message": message,
        "kind": kind,
        "name": name,
        "suggested_tool": "find_capabilities",
        "suggested_arguments": {"query": query, "limit": 10},
    }


def suggested_cli_actions_for_request(
    *,
    request_type: CapabilityRequestType,
    package_ref: str | None = None,
) -> list[str]:
    """Return stable next-step CLI actions for one request type."""
    if request_type == "reload_runtime":
        return ["/runtime reload"]
    if request_type == "install_extension":
        actions: list[str] = []
        if package_ref:
            actions.append(f"/extension install {package_ref}")
        actions.append("/runtime reload")
        return actions
    if request_type == "enable_config":
        return ["Edit config.yaml to enable the missing capability", "/runtime reload"]
    return []


@dataclass(frozen=True)
class CapabilityCandidate:
    """One search result from the capability inventory."""

    kind: str
    name: str
    description: str
    availability: CapabilityAvailability
    reason_unavailable: str | None = None
    tool_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    extension_name: str | None = None
    package_ref: str | None = None
    suggested_cli_actions: tuple[str, ...] = ()
    _score: int = 0

    def to_payload(self) -> dict[str, Any]:
        """Render a JSON-safe search result."""
        return {
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "availability": self.availability,
            "reason_unavailable": self.reason_unavailable,
            "tool_names": list(self.tool_names),
            "skill_names": list(self.skill_names),
            "extension_name": self.extension_name,
            "package_ref": self.package_ref,
            "suggested_cli_actions": list(self.suggested_cli_actions),
        }


@dataclass
class CapabilityRequest:
    """One missing-capability request raised during a live session."""

    request_id: str
    status: CapabilityRequestStatus
    request_type: CapabilityRequestType
    summary: str
    reason: str
    desired_capability: str
    suggested_cli_actions: list[str]
    created_at: str
    updated_at: str
    occurrence_count: int = 1
    package_ref: str | None = None
    extension_name: str | None = None
    skill_name: str | None = None
    tool_name: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Render a JSON-safe request record."""
        return {
            "request_id": self.request_id,
            "status": self.status,
            "request_type": self.request_type,
            "summary": self.summary,
            "reason": self.reason,
            "desired_capability": self.desired_capability,
            "package_ref": self.package_ref,
            "extension_name": self.extension_name,
            "skill_name": self.skill_name,
            "tool_name": self.tool_name,
            "suggested_cli_actions": list(self.suggested_cli_actions),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "occurrence_count": self.occurrence_count,
        }


class CapabilityRequestManager:
    """Track pending missing-capability requests for one live session."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: dict[str, CapabilityRequest] = {}

    def create_or_update(
        self,
        *,
        summary: str,
        reason: str,
        desired_capability: str,
        request_type: CapabilityRequestType,
        package_ref: str | None = None,
        extension_name: str | None = None,
        skill_name: str | None = None,
        tool_name: str | None = None,
        suggested_cli_actions: list[str] | None = None,
    ) -> CapabilityRequest:
        """Create a new request or merge into an existing pending one."""
        normalized_key = self._build_request_key(
            request_type=request_type,
            desired_capability=desired_capability,
            package_ref=package_ref,
            extension_name=extension_name,
            skill_name=skill_name,
            tool_name=tool_name,
        )
        now = _now_iso()
        actions = list(
            suggested_cli_actions
            if suggested_cli_actions is not None
            else suggested_cli_actions_for_request(
                request_type=request_type,
                package_ref=package_ref,
            )
        )

        with self._lock:
            existing = self._find_pending_by_key(normalized_key)
            if existing is not None:
                existing.summary = summary
                existing.reason = reason
                existing.desired_capability = desired_capability
                existing.updated_at = now
                existing.occurrence_count += 1
                existing.package_ref = existing.package_ref or package_ref
                existing.extension_name = existing.extension_name or extension_name
                existing.skill_name = existing.skill_name or skill_name
                existing.tool_name = existing.tool_name or tool_name
                existing.suggested_cli_actions = actions
                return existing

            request = CapabilityRequest(
                request_id=f"capreq_{uuid4().hex[:12]}",
                status="pending",
                request_type=request_type,
                summary=summary,
                reason=reason,
                desired_capability=desired_capability,
                package_ref=package_ref,
                extension_name=extension_name,
                skill_name=skill_name,
                tool_name=tool_name,
                suggested_cli_actions=actions,
                created_at=now,
                updated_at=now,
            )
            self._requests[request.request_id] = request
            return request

    def list_requests(
        self,
        *,
        status: CapabilityRequestStatus | None = None,
    ) -> list[CapabilityRequest]:
        """Return requests sorted newest-first within status buckets."""
        with self._lock:
            requests = list(self._requests.values())
        if status is not None:
            requests = [request for request in requests if request.status == status]
        requests = sorted(requests, key=lambda item: item.updated_at, reverse=True)
        return sorted(requests, key=lambda item: item.status != "pending")

    def get_request(self, request_id: str) -> CapabilityRequest | None:
        """Return one request by id."""
        with self._lock:
            return self._requests.get(request_id)

    def dismiss_request(self, request_id: str) -> CapabilityRequest:
        """Mark one request dismissed."""
        return self._set_status(request_id, "dismissed")

    def resolve_request(self, request_id: str) -> CapabilityRequest:
        """Mark one request resolved."""
        return self._set_status(request_id, "resolved")

    def pending_count(self) -> int:
        """Return the number of pending requests."""
        return len(self.list_requests(status="pending"))

    def auto_resolve(
        self,
        *,
        tool_registry,
        skill_manager: SkillManager | None,
        extension_manager: ExtensionManager | None,
    ) -> list[str]:
        """Resolve exact pending requests whose target is now available."""
        resolved: list[str] = []
        with self._lock:
            for request in self._requests.values():
                if request.status != "pending":
                    continue
                if not self._request_is_satisfied(
                    request,
                    tool_registry=tool_registry,
                    skill_manager=skill_manager,
                    extension_manager=extension_manager,
                ):
                    continue
                request.status = "resolved"
                request.updated_at = _now_iso()
                resolved.append(request.request_id)
        return sorted(resolved)

    def _set_status(
        self,
        request_id: str,
        status: CapabilityRequestStatus,
    ) -> CapabilityRequest:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise KeyError(request_id)
            request.status = status
            request.updated_at = _now_iso()
            return request

    def _find_pending_by_key(self, normalized_key: str) -> CapabilityRequest | None:
        for request in self._requests.values():
            if request.status != "pending":
                continue
            if self._build_request_key(
                request_type=request.request_type,
                desired_capability=request.desired_capability,
                package_ref=request.package_ref,
                extension_name=request.extension_name,
                skill_name=request.skill_name,
                tool_name=request.tool_name,
            ) == normalized_key:
                return request
        return None

    @staticmethod
    def _build_request_key(
        *,
        request_type: CapabilityRequestType,
        desired_capability: str,
        package_ref: str | None,
        extension_name: str | None,
        skill_name: str | None,
        tool_name: str | None,
    ) -> str:
        for value in (package_ref, extension_name, skill_name, tool_name):
            if value:
                return f"{request_type}:{normalize_capability_text(value)}"
        return f"{request_type}:{normalize_capability_text(desired_capability)}"

    @staticmethod
    def _request_is_satisfied(
        request: CapabilityRequest,
        *,
        tool_registry,
        skill_manager: SkillManager | None,
        extension_manager: ExtensionManager | None,
    ) -> bool:
        if request.request_type == "generic":
            return False

        if request.tool_name and tool_registry is not None and tool_registry.get(request.tool_name) is not None:
            return True

        if request.skill_name and skill_manager is not None:
            skill = skill_manager.get_skill(request.skill_name)
            if skill is not None and skill.eligible:
                return True

        if request.extension_name and extension_manager is not None:
            if extension_manager.get_extension(request.extension_name) is not None:
                return True

        if request.package_ref and extension_manager is not None and ":" in request.package_ref:
            package_name = request.package_ref.split(":", 1)[1]
            if extension_manager.get_extension(package_name) is not None:
                return True

        return False


class CapabilityInventory:
    """Search current and installable capabilities without activating them."""

    def __init__(
        self,
        *,
        repo_root: Path,
        runtime_config,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.runtime_config = runtime_config
        self.tool_registry = None
        self.skill_manager: SkillManager | None = None
        self.extension_manager: ExtensionManager | None = None

    def bind_runtime(
        self,
        *,
        tool_registry,
        skill_manager: SkillManager | None,
        extension_manager: ExtensionManager | None,
    ) -> None:
        """Attach the current live runtime view used for active capability checks."""
        self.tool_registry = tool_registry
        self.skill_manager = skill_manager
        self.extension_manager = extension_manager

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search active, discoverable, and installable capabilities."""
        normalized_query = normalize_capability_text(query)
        if not normalized_query:
            return []

        candidates: list[CapabilityCandidate] = []
        candidates.extend(self._collect_active_tool_candidates(normalized_query))
        candidates.extend(self._collect_active_skill_candidates(normalized_query))
        candidates.extend(self._collect_discoverable_candidates(normalized_query))
        candidates.extend(self._collect_catalog_candidates(normalized_query))

        deduped: dict[tuple[str, str, str, str | None], CapabilityCandidate] = {}
        for candidate in candidates:
            key = (candidate.kind, candidate.name, candidate.availability, candidate.package_ref)
            current = deduped.get(key)
            if current is None or candidate._score > current._score:
                deduped[key] = candidate

        ranked = sorted(
            deduped.values(),
            key=lambda item: (-item._score, item.kind, item.name),
        )
        return [candidate.to_payload() for candidate in ranked[:limit]]

    def _collect_active_tool_candidates(self, query: str) -> list[CapabilityCandidate]:
        if self.tool_registry is None:
            return []

        candidates: list[CapabilityCandidate] = []
        for tool_name in sorted(self.tool_registry.list_tools()):
            tool = self.tool_registry.get(tool_name)
            description = str(getattr(tool, "description", "") or "")
            score = self._score_match(query, tool_name, description)
            if score <= 0:
                continue
            candidates.append(
                CapabilityCandidate(
                    kind="tool",
                    name=tool_name,
                    description=description,
                    availability="active",
                    tool_names=(tool_name,),
                    _score=score,
                )
            )
        return candidates

    def _collect_active_skill_candidates(self, query: str) -> list[CapabilityCandidate]:
        if self.skill_manager is None:
            return []

        candidates: list[CapabilityCandidate] = []
        for skill in self.skill_manager.list_skills():
            description = skill.short_description or skill.description
            score = self._score_match(query, skill.name, description)
            if score <= 0:
                continue
            availability: CapabilityAvailability = "loadable" if skill.eligible else "ineligible"
            actions = ()
            if not skill.eligible:
                actions = tuple(
                    suggested_cli_actions_for_request(request_type="enable_config")
                )
            candidates.append(
                CapabilityCandidate(
                    kind="skill",
                    name=skill.name,
                    description=description,
                    availability=availability,
                    reason_unavailable=skill.eligibility_reason,
                    skill_names=(skill.name,),
                    extension_name=skill.extension_name,
                    suggested_cli_actions=actions,
                    _score=score,
                )
            )
        return candidates

    def _collect_discoverable_candidates(self, query: str) -> list[CapabilityCandidate]:
        if not self.runtime_config.extensions.enabled:
            return []

        fresh_extension_manager = ExtensionManager(repo_root=self.repo_root, runtime_config=self.runtime_config)
        fresh_extension_manager.discover()
        fresh_skill_manager = SkillManager(
            repo_root=self.repo_root,
            runtime_config=self.runtime_config,
            extra_roots=fresh_extension_manager.get_skill_roots(),
        )
        fresh_skill_manager.discover()

        active_tool_names = (
            set(self.tool_registry.list_tools())
            if self.tool_registry is not None
            else set()
        )
        active_skill_names = (
            {skill.name for skill in self.skill_manager.list_skills()}
            if self.skill_manager is not None
            else set()
        )
        active_extension_names = (
            {extension.name for extension in self.extension_manager.list_extensions()}
            if self.extension_manager is not None
            else set()
        )

        skill_names_by_extension: dict[str, list[str]] = {}
        for skill in fresh_skill_manager.list_skills():
            if not skill.extension_name:
                continue
            skill_names_by_extension.setdefault(skill.extension_name, []).append(skill.name)

        candidates: list[CapabilityCandidate] = []
        for extension in fresh_extension_manager.list_extensions():
            extension_skill_names = tuple(sorted(skill_names_by_extension.get(extension.name, [])))
            extension_tool_names = tuple(tool.name for tool in extension.tool_specs)
            extension_availability: CapabilityAvailability = (
                "active" if extension.name in active_extension_names else "reload_required"
            )
            extension_reason = None
            extension_actions: tuple[str, ...] = ()
            if extension_availability == "reload_required":
                extension_reason = "Extension bundle exists on disk but is not active in this runtime"
                extension_actions = tuple(
                    suggested_cli_actions_for_request(request_type="reload_runtime")
                )
            extension_score = self._score_match(
                query,
                extension.name,
                extension.description,
                *extension_tool_names,
                *extension_skill_names,
            )
            if extension_score > 0:
                candidates.append(
                    CapabilityCandidate(
                        kind="extension",
                        name=extension.name,
                        description=extension.description,
                        availability=extension_availability,
                        reason_unavailable=extension_reason,
                        tool_names=extension_tool_names,
                        skill_names=extension_skill_names,
                        extension_name=extension.name,
                        suggested_cli_actions=extension_actions,
                        _score=extension_score,
                    )
                )

            if extension_availability != "reload_required":
                continue

            for tool_spec in extension.tool_specs:
                if tool_spec.name in active_tool_names:
                    continue
                score = self._score_match(query, tool_spec.name, tool_spec.description, extension.name)
                if score <= 0:
                    continue
                candidates.append(
                    CapabilityCandidate(
                        kind="tool",
                        name=tool_spec.name,
                        description=tool_spec.description,
                        availability="reload_required",
                        reason_unavailable="Tool is defined by an on-disk extension bundle but not active yet",
                        tool_names=(tool_spec.name,),
                        skill_names=extension_skill_names,
                        extension_name=extension.name,
                        suggested_cli_actions=tuple(
                            suggested_cli_actions_for_request(request_type="reload_runtime")
                        ),
                        _score=score,
                    )
                )

        for skill in fresh_skill_manager.list_skills():
            if skill.name in active_skill_names:
                continue
            description = skill.short_description or skill.description
            score = self._score_match(query, skill.name, description, skill.extension_name or "")
            if score <= 0:
                continue
            availability: CapabilityAvailability = "reload_required" if skill.eligible else "ineligible"
            reason_unavailable = (
                "Skill bundle exists on disk but is not active in this runtime"
                if skill.eligible
                else skill.eligibility_reason
            )
            actions = (
                tuple(suggested_cli_actions_for_request(request_type="reload_runtime"))
                if skill.eligible
                else tuple(suggested_cli_actions_for_request(request_type="enable_config"))
            )
            candidates.append(
                CapabilityCandidate(
                    kind="skill",
                    name=skill.name,
                    description=description,
                    availability=availability,
                    reason_unavailable=reason_unavailable,
                    skill_names=(skill.name,),
                    extension_name=skill.extension_name,
                    suggested_cli_actions=actions,
                    _score=score,
                )
            )

        return candidates

    def _collect_catalog_candidates(self, query: str) -> list[CapabilityCandidate]:
        if self.extension_manager is None:
            return []

        candidates: list[CapabilityCandidate] = []
        for package in self.extension_manager.list_catalog_packages():
            package_ref = f"{package.catalog_name}:{package.name}"
            description = package.description or f"Installable extension package {package.name}"
            score = self._score_match(query, package.name, description, package_ref)
            if score <= 0:
                continue
            candidates.append(
                CapabilityCandidate(
                    kind="catalog_package",
                    name=package.name,
                    description=description,
                    availability="installable",
                    reason_unavailable="Capability is available from a configured extension catalog",
                    extension_name=package.name,
                    package_ref=package_ref,
                    suggested_cli_actions=tuple(
                        suggested_cli_actions_for_request(
                            request_type="install_extension",
                            package_ref=package_ref,
                        )
                    ),
                    _score=score,
                )
            )
        return candidates

    @staticmethod
    def _score_match(query: str, *fields: str) -> int:
        haystacks = [normalize_capability_text(field) for field in fields if field]
        if not haystacks:
            return 0

        score = 0
        for haystack in haystacks:
            if not haystack:
                continue
            if haystack == query:
                score = max(score, 120)
            elif query in haystack:
                score = max(score, 70)
            else:
                query_tokens = [token for token in query.split(" ") if token]
                if query_tokens and all(token in haystack for token in query_tokens):
                    score = max(score, 40 + len(query_tokens))
        return score
