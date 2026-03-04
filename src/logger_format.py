"""Formatting helpers for session timeline logs."""

from __future__ import annotations

import json
from typing import Any, List, Optional


SESSION_HEADER_RULE = "=" * 80
SECTION_RULE = "-" * 80
ARTIFACT_SPILL_THRESHOLD = 8192

ID_PADDING_WIDTH = 4
ITERATION_PADDING_WIDTH = 2
UUID_TRUNC_LENGTH = 8


def append_json_block(label: str, payload: Any) -> List[str]:
    """Render a JSON block for llm.log."""
    return [label, "<<<json", pretty_json(payload), ">>>", ""]


def append_text_block(label: str, text: str) -> List[str]:
    """Render a text block for llm.log."""
    return [label, "<<<", text or "", ">>>", ""]


def render_timeline_block(
    *,
    rule: str,
    header: str,
    timestamp: str,
    metadata_lines: Optional[List[str]] = None,
    sections: Optional[List[str]] = None,
) -> str:
    """Build one fully formatted timeline block."""
    lines = [rule, header, f"Timestamp: {timestamp}"]
    if metadata_lines:
        lines.extend(metadata_lines)
    lines.extend([rule, ""])
    if sections:
        lines.extend(sections)
    return "".join(f"{line}\n" for line in lines)


def pretty_json(payload: Any) -> str:
    """Pretty-print JSON with stable formatting."""
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def serialize_payload(payload: Any) -> str:
    """Serialize a payload for size checks."""
    if isinstance(payload, str):
        return payload
    return pretty_json(payload)
