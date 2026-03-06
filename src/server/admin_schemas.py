"""Schema helpers for Kubernetes-style admin resources."""

from __future__ import annotations

from time import time
from typing import Any


API_VERSION = "nano-claw/v1"


def new_resource_version() -> str:
    """Return a millisecond resource-version marker."""
    return str(int(time() * 1000))


def build_resource(
    *,
    kind: str,
    name: str,
    spec: dict[str, Any],
    status: dict[str, Any],
    resource_version: str | None = None,
    metadata_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one resource envelope."""
    metadata = {
        "name": name,
        "resourceVersion": resource_version or new_resource_version(),
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    return {
        "apiVersion": API_VERSION,
        "kind": kind,
        "metadata": metadata,
        "spec": spec,
        "status": status,
    }


def build_list_resource(
    *,
    kind: str,
    items: list[dict[str, Any]],
    count: int | None = None,
    next_cursor: str | None = None,
    resource_version: str | None = None,
    metadata_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one list resource envelope."""
    metadata: dict[str, Any] = {
        "resourceVersion": resource_version or new_resource_version(),
        "count": len(items) if count is None else count,
        "nextCursor": next_cursor,
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    return {
        "apiVersion": API_VERSION,
        "kind": f"{kind}List",
        "metadata": metadata,
        "items": items,
    }
