"""Built-in tools for missing-capability discovery and request tracking."""

from __future__ import annotations

from typing import Any

from src.capabilities import CapabilityInventory, CapabilityRequestManager
from src.tools import Tool, ToolResult


_REQUEST_TYPES = {
    "reload_runtime",
    "install_extension",
    "enable_config",
    "generic",
}


class FindCapabilitiesTool(Tool):
    """Search active, discoverable, and installable capabilities."""

    name = "find_capabilities"
    description = (
        "Search current tools, skills, discovered extensions, and configured extension catalogs "
        "before requesting a missing capability."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Capability, tool, skill, or extension name to search for.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of matching capability records to return.",
                "default": 10,
                "minimum": 1,
                "maximum": 25,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, capability_inventory: CapabilityInventory) -> None:
        self._capability_inventory = capability_inventory

    def execute(self, context, **kwargs) -> ToolResult:
        query = self._require_param(kwargs, "query")
        if not isinstance(query, str):
            return ToolResult(success=False, error="query must be a string")

        limit = kwargs.get("limit", 10)
        if not isinstance(limit, int):
            return ToolResult(success=False, error="limit must be an integer")
        if limit < 1 or limit > 25:
            return ToolResult(success=False, error="limit must be between 1 and 25")

        return ToolResult(
            success=True,
            data={
                "query": query,
                "matches": self._capability_inventory.search(query, limit=limit),
            },
        )


class RequestCapabilityTool(Tool):
    """Record or update a missing-capability request for the current session."""

    name = "request_capability"
    description = (
        "Create or update a structured missing-capability request for this session. "
        "Use this after inspecting capabilities, then also explain the need in plain text."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Short one-line summary of what capability is missing.",
            },
            "reason": {
                "type": "string",
                "description": "Why the current task needs this capability.",
            },
            "desired_capability": {
                "type": "string",
                "description": "Human-readable name for the desired capability.",
            },
            "request_type": {
                "type": "string",
                "enum": sorted(_REQUEST_TYPES),
                "description": "Whether the request needs reload, install, config enablement, or is generic.",
            },
            "package_ref": {
                "type": "string",
                "description": "Optional exact extension package reference in <catalog>:<package> form.",
            },
            "extension_name": {
                "type": "string",
                "description": "Optional exact extension bundle name when known.",
            },
            "skill_name": {
                "type": "string",
                "description": "Optional exact skill name when known.",
            },
            "tool_name": {
                "type": "string",
                "description": "Optional exact tool name when known.",
            },
        },
        "required": ["summary", "reason", "desired_capability", "request_type"],
        "additionalProperties": False,
    }

    def __init__(self, capability_request_manager: CapabilityRequestManager) -> None:
        self._capability_request_manager = capability_request_manager

    def execute(self, context, **kwargs) -> ToolResult:
        request_type = kwargs.get("request_type")
        if not isinstance(request_type, str) or request_type not in _REQUEST_TYPES:
            return ToolResult(
                success=False,
                error="request_type must be one of: reload_runtime, install_extension, enable_config, generic",
            )

        normalized_fields: dict[str, str | None] = {}
        for field_name in (
            "summary",
            "reason",
            "desired_capability",
            "package_ref",
            "extension_name",
            "skill_name",
            "tool_name",
        ):
            value = kwargs.get(field_name)
            if value is None:
                normalized_fields[field_name] = None
                continue
            if not isinstance(value, str):
                return ToolResult(success=False, error=f"{field_name} must be a string")
            trimmed = value.strip()
            if field_name in {"summary", "reason", "desired_capability"} and not trimmed:
                return ToolResult(success=False, error=f"{field_name} must not be empty")
            normalized_fields[field_name] = trimmed or None

        request = self._capability_request_manager.create_or_update(
            summary=normalized_fields["summary"] or "",
            reason=normalized_fields["reason"] or "",
            desired_capability=normalized_fields["desired_capability"] or "",
            request_type=request_type,
            package_ref=normalized_fields["package_ref"],
            extension_name=normalized_fields["extension_name"],
            skill_name=normalized_fields["skill_name"],
            tool_name=normalized_fields["tool_name"],
        )
        return ToolResult(success=True, data=request.to_payload())
