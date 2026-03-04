"""Shared helpers for safe live activity previews."""

from __future__ import annotations

import json
from typing import Any

_MAX_SINGLE_LINE = 120
_MAX_BODY_CHARS = 800
_MAX_BODY_LINES = 10


def build_tool_signature(
    tool_name: str | None,
    arguments: Any,
    *,
    max_items: int = 2,
    max_length: int = 96,
) -> str:
    """Build a compact user-facing tool signature."""
    base = tool_name or "tool"
    if not isinstance(arguments, dict) or not arguments:
        return base

    parts = []
    for index, (key, value) in enumerate(arguments.items()):
        if index >= max_items:
            parts.append("...")
            break
        parts.append(f"{key}={_format_argument_value(value)}")

    signature = f"{base}({', '.join(parts)})"
    if len(signature) <= max_length:
        return signature
    return signature[: max_length - 3].rstrip() + "..."


def build_assistant_preview(text: str, tool_signatures: list[str]) -> tuple[str, str]:
    """Build a safe preview and body for assistant-visible content."""
    body = _sanitize_body(text)
    if body:
        return _truncate_single_line(body), body

    if tool_signatures:
        tool_word = "tool" if len(tool_signatures) == 1 else "tools"
        preview = f"Requested {len(tool_signatures)} {tool_word}"
        body = "\n".join(f"- {signature}" for signature in tool_signatures[:_MAX_BODY_LINES])
        return preview, body

    return "", ""


def build_tool_result_preview(tool_name: str, result: dict[str, Any]) -> tuple[str, str]:
    """Build a safe preview and body for a tool result payload."""
    if not isinstance(result, dict):
        body = _sanitize_body(str(result))
        return _truncate_single_line(body), body

    if result.get("error"):
        body = _sanitize_body(str(result["error"]))
        return _truncate_single_line(body), body

    output = result.get("output", "")
    if tool_name == "read_file" and isinstance(output, str):
        body = _sanitize_body(output)
        preview = _truncate_single_line(_first_meaningful_line(body) or "Read file")
        return preview, body

    if tool_name == "write_file" and isinstance(output, str):
        body = _sanitize_body(output)
        return _truncate_single_line(body or "Wrote file"), body

    if tool_name == "write_plan" and isinstance(output, str):
        body = _sanitize_body(output)
        return _truncate_single_line(body or "Updated plan"), body

    if tool_name == "run_command":
        body = _sanitize_body(str(output))
        return _truncate_single_line(body or "Command completed"), body

    if tool_name == "run_readonly_command":
        body = _sanitize_body(str(output))
        return _truncate_single_line(body or "Read-only command completed"), body

    if tool_name == "submit_plan":
        body = _sanitize_body(
            str(result.get("report") or result.get("summary") or output or "Plan submitted")
        )
        return _truncate_single_line(body or "Plan submitted"), body

    if tool_name == "load_skill" and isinstance(output, str):
        body = _sanitize_body(output)
        return _truncate_single_line(body or "Loaded skill"), body

    if isinstance(output, str):
        body = _sanitize_body(output)
        return _truncate_single_line(body), body

    try:
        body = _sanitize_body(
            json.dumps(output, ensure_ascii=True, sort_keys=True, indent=2)
        )
    except TypeError:
        body = _sanitize_body(repr(output))
    return _truncate_single_line(body), body


def _format_argument_value(value: Any) -> str:
    """Format one tool argument value for compact display."""
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > 36:
            compact = compact[:33] + "..."
        return repr(compact)

    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)

    try:
        compact = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except TypeError:
        compact = repr(value)

    if len(compact) > 36:
        compact = compact[:33] + "..."
    return compact


def _sanitize_body(text: str) -> str:
    """Normalize, truncate, and keep a short multiline body preview."""
    if not text:
        return ""

    lines = [line.rstrip() for line in str(text).splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    truncated = []
    for line in lines[:_MAX_BODY_LINES]:
        truncated.append(line[:_MAX_SINGLE_LINE].rstrip())

    body = "\n".join(truncated)
    if len(body) > _MAX_BODY_CHARS:
        body = body[: _MAX_BODY_CHARS - 3].rstrip() + "..."
    return body


def _truncate_single_line(text: str) -> str:
    """Collapse a body to one safe preview line."""
    single_line = " ".join(text.split())
    if len(single_line) > _MAX_SINGLE_LINE:
        return single_line[: _MAX_SINGLE_LINE - 3].rstrip() + "..."
    return single_line


def _first_meaningful_line(text: str) -> str:
    """Return the first non-empty line from a multiline preview."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""
