"""macOS helper-backed tools for Finder, Calendar, Notes, Reminders, and Messages."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from src.tools import Tool, ToolResult


class MacOSHelperError(Exception):
    """Base macOS helper failure."""


class MacOSPermissionError(MacOSHelperError):
    """Automation permission failure from the macOS helper."""


class MacOSOutputError(MacOSHelperError):
    """Malformed or empty helper output."""


@dataclass
class MacOSHelper:
    """Execute the shared JXA helper and normalize its output."""

    timeout_seconds: int = 10
    script_path: Path | None = None

    def __post_init__(self) -> None:
        if self.script_path is None:
            self.script_path = Path(__file__).with_name("macos_helper.js")

    def execute(self, *, app: str, action: str, arguments: dict[str, Any]) -> Any:
        payload = {"app": app, "action": action, "args": arguments}

        try:
            completed = subprocess.run(
                ["osascript", "-l", "JavaScript", str(self.script_path)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MacOSHelperError(
                f"macOS helper timed out after {self.timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise MacOSHelperError(f"Failed to execute osascript: {exc}") from exc

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()

        if completed.returncode != 0:
            raise self._map_error(stdout=stdout, stderr=stderr)

        if not stdout:
            raise MacOSOutputError("macOS helper returned no output")

        try:
            payload_data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise MacOSOutputError("macOS helper returned malformed JSON") from exc

        if not isinstance(payload_data, dict):
            raise MacOSOutputError("macOS helper returned an invalid response payload")

        if payload_data.get("ok") is True:
            return payload_data.get("data")

        error = payload_data.get("error")
        if not isinstance(error, dict):
            raise MacOSHelperError("macOS helper reported an unknown error")
        raise self._map_structured_error(error)

    def _map_error(self, *, stdout: str, stderr: str) -> MacOSHelperError:
        combined = "\n".join(part for part in (stdout, stderr) if part).strip()
        message = combined or "macOS helper failed"
        if "-1743" in message or "Not authorized to send Apple events" in message:
            return MacOSPermissionError(self._permission_message())
        return MacOSHelperError(message)

    def _map_structured_error(self, error: dict[str, Any]) -> MacOSHelperError:
        code = str(error.get("code") or "helper_error")
        message = str(error.get("message") or "macOS helper failed")
        if code == "permission_denied":
            return MacOSPermissionError(self._permission_message())
        return MacOSHelperError(message)

    @staticmethod
    def _permission_message() -> str:
        return (
            "macOS Automation permission denied. Grant Automation access in "
            "System Settings > Privacy & Security > Automation and try again."
        )


def _require_int(
    kwargs: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int = 1,
    maximum: int = 1000,
) -> int:
    value = kwargs.get(name, default)
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _require_bool(kwargs: dict[str, Any], name: str, *, default: bool = False) -> bool:
    value = kwargs.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _require_string(kwargs: dict[str, Any], name: str) -> str:
    value = kwargs.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _optional_string(kwargs: dict[str, Any], name: str) -> str | None:
    value = kwargs.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    stripped = value.strip()
    return stripped or None


def _require_iso_datetime(kwargs: dict[str, Any], name: str) -> str:
    value = _require_string(kwargs, name)
    return _validate_iso_datetime(name, value)


def _validate_iso_datetime(name: str, value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include an explicit timezone offset")
    return value


def _optional_iso_datetime(kwargs: dict[str, Any], name: str) -> str | None:
    value = _optional_string(kwargs, name)
    if value is None:
        return None
    return _validate_iso_datetime(name, value)


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date_only(name: str, value: str) -> str:
    if not _DATE_ONLY_RE.fullmatch(value):
        raise ValueError(f"{name} must be a valid YYYY-MM-DD date")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid YYYY-MM-DD date") from exc
    return value


def _require_date_only(kwargs: dict[str, Any], name: str) -> str:
    value = _require_string(kwargs, name)
    return _validate_date_only(name, value)


def _optional_date_only(kwargs: dict[str, Any], name: str) -> str | None:
    value = _optional_string(kwargs, name)
    if value is None:
        return None
    return _validate_date_only(name, value)


def _resolve_local_path(context, raw_path: str) -> str:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = context.cwd / candidate
    return str(candidate.resolve())


class FinderActionTool(Tool):
    """Bounded Finder/file-management helper tool."""

    name = "finder_action"
    description = (
        "Access Finder-style local file actions on macOS. Supported actions: "
        "list_items, open_item, reveal_item, create_folder, rename_item."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_items", "open_item", "reveal_item", "create_folder", "rename_item"],
                "description": "Finder action to execute.",
            },
            "path": {"type": "string", "description": "Target path for list/open/reveal/rename actions."},
            "parent_path": {"type": "string", "description": "Parent folder path for create_folder."},
            "name": {"type": "string", "description": "Folder name to create."},
            "new_name": {"type": "string", "description": "Replacement item name for rename_item."},
            "include_hidden": {"type": "boolean", "description": "Include dotfiles for list_items."},
            "limit": {"type": "integer", "description": "Maximum list_items results.", "default": 200},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, helper: MacOSHelper) -> None:
        self.helper = helper

    def execute(self, context, **kwargs) -> ToolResult:
        try:
            action = _require_string(kwargs, "action")
            arguments = self._build_arguments(context, action, kwargs)
            data = self.helper.execute(app="finder", action=action, arguments=arguments)
            return ToolResult(success=True, data=data)
        except (ValueError, MacOSHelperError) as exc:
            return ToolResult(success=False, error=str(exc))

    def _build_arguments(self, context, action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if action == "list_items":
            path = _resolve_local_path(context, _require_string(kwargs, "path"))
            return {
                "path": path,
                "include_hidden": _require_bool(kwargs, "include_hidden", default=False),
                "limit": _require_int(kwargs, "limit", default=200),
            }
        if action in {"open_item", "reveal_item"}:
            return {"path": _resolve_local_path(context, _require_string(kwargs, "path"))}
        if action == "create_folder":
            return {
                "parent_path": _resolve_local_path(context, _require_string(kwargs, "parent_path")),
                "name": _require_string(kwargs, "name"),
            }
        if action == "rename_item":
            return {
                "path": _resolve_local_path(context, _require_string(kwargs, "path")),
                "new_name": _require_string(kwargs, "new_name"),
            }
        raise ValueError(f"Unsupported finder action: {action}")


class CalendarActionTool(Tool):
    """Bounded Calendar.app helper tool."""

    name = "calendar_action"
    description = (
        "Access Calendar.app on macOS. Supported actions: list_calendars, list_events, "
        "create_event, update_event."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_calendars", "list_events", "create_event", "update_event"],
                "description": "Calendar action to execute.",
            },
            "calendar_name": {"type": "string", "description": "Calendar name for event actions."},
            "start_at": {"type": "string", "description": "ISO 8601 start datetime with timezone."},
            "end_at": {"type": "string", "description": "ISO 8601 end datetime with timezone."},
            "event_id": {"type": "string", "description": "Opaque event id returned by prior calls."},
            "title": {"type": "string", "description": "Event title."},
            "location": {"type": "string", "description": "Event location."},
            "notes": {"type": "string", "description": "Event notes."},
            "query": {"type": "string", "description": "Optional substring filter for list_events."},
            "limit": {"type": "integer", "description": "Maximum list_events results.", "default": 100},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, helper: MacOSHelper) -> None:
        self.helper = helper

    def execute(self, context, **kwargs) -> ToolResult:
        del context
        try:
            action = _require_string(kwargs, "action")
            arguments = self._build_arguments(action, kwargs)
            data = self.helper.execute(app="calendar", action=action, arguments=arguments)
            return ToolResult(success=True, data=data)
        except (ValueError, MacOSHelperError) as exc:
            return ToolResult(success=False, error=str(exc))

    def _build_arguments(self, action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if action == "list_calendars":
            return {}
        if action == "list_events":
            return {
                "calendar_name": _optional_string(kwargs, "calendar_name"),
                "start_at": _require_iso_datetime(kwargs, "start_at"),
                "end_at": _require_iso_datetime(kwargs, "end_at"),
                "query": _optional_string(kwargs, "query"),
                "limit": _require_int(kwargs, "limit", default=100),
            }
        if action == "create_event":
            start_at = _require_iso_datetime(kwargs, "start_at")
            end_at = _require_iso_datetime(kwargs, "end_at")
            if datetime.fromisoformat(end_at) <= datetime.fromisoformat(start_at):
                raise ValueError("end_at must be later than start_at")
            return {
                "calendar_name": _require_string(kwargs, "calendar_name"),
                "title": _require_string(kwargs, "title"),
                "start_at": start_at,
                "end_at": end_at,
                "location": _optional_string(kwargs, "location"),
                "notes": _optional_string(kwargs, "notes"),
            }
        if action == "update_event":
            updates = {
                "title": _optional_string(kwargs, "title"),
                "start_at": _optional_string(kwargs, "start_at"),
                "end_at": _optional_string(kwargs, "end_at"),
                "location": _optional_string(kwargs, "location"),
                "notes": _optional_string(kwargs, "notes"),
            }
            if updates["start_at"] is not None:
                updates["start_at"] = _require_iso_datetime(updates, "start_at")
            if updates["end_at"] is not None:
                updates["end_at"] = _require_iso_datetime(updates, "end_at")
            if updates["start_at"] and updates["end_at"]:
                if datetime.fromisoformat(updates["end_at"]) <= datetime.fromisoformat(updates["start_at"]):
                    raise ValueError("end_at must be later than start_at")
            if not any(value is not None for value in updates.values()):
                raise ValueError("update_event requires at least one updated field")
            return {"event_id": _require_string(kwargs, "event_id"), **updates}
        raise ValueError(f"Unsupported calendar action: {action}")


class NotesActionTool(Tool):
    """Bounded Notes.app helper tool."""

    name = "notes_action"
    description = (
        "Access Notes.app on macOS. Supported actions: list_notes, read_note, create_note, update_note."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_notes", "read_note", "create_note", "update_note"],
                "description": "Notes action to execute.",
            },
            "folder_name": {"type": "string", "description": "Folder name for notes queries or creation."},
            "query": {"type": "string", "description": "Optional substring filter for list_notes."},
            "limit": {"type": "integer", "description": "Maximum list_notes results.", "default": 100},
            "note_id": {"type": "string", "description": "Opaque note id returned by prior calls."},
            "title": {"type": "string", "description": "Note title."},
            "body_text": {"type": "string", "description": "Note body text."},
            "body_mode": {
                "type": "string",
                "enum": ["replace", "append"],
                "description": "Update behavior for body_text.",
                "default": "replace",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, helper: MacOSHelper) -> None:
        self.helper = helper

    def execute(self, context, **kwargs) -> ToolResult:
        del context
        try:
            action = _require_string(kwargs, "action")
            arguments = self._build_arguments(action, kwargs)
            data = self.helper.execute(app="notes", action=action, arguments=arguments)
            return ToolResult(success=True, data=data)
        except (ValueError, MacOSHelperError) as exc:
            return ToolResult(success=False, error=str(exc))

    def _build_arguments(self, action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if action == "list_notes":
            return {
                "folder_name": _optional_string(kwargs, "folder_name"),
                "query": _optional_string(kwargs, "query"),
                "limit": _require_int(kwargs, "limit", default=100),
            }
        if action == "read_note":
            return {"note_id": _require_string(kwargs, "note_id")}
        if action == "create_note":
            return {
                "folder_name": _require_string(kwargs, "folder_name"),
                "title": _require_string(kwargs, "title"),
                "body_text": _require_string(kwargs, "body_text"),
            }
        if action == "update_note":
            title = _optional_string(kwargs, "title")
            body_text = _optional_string(kwargs, "body_text")
            body_mode = kwargs.get("body_mode", "replace")
            if body_mode not in {"replace", "append"}:
                raise ValueError("body_mode must be replace or append")
            if title is None and body_text is None:
                raise ValueError("update_note requires at least one updated field")
            return {
                "note_id": _require_string(kwargs, "note_id"),
                "title": title,
                "body_text": body_text,
                "body_mode": body_mode,
            }
        raise ValueError(f"Unsupported notes action: {action}")


class RemindersActionTool(Tool):
    """Bounded Reminders.app helper tool."""

    name = "reminders_action"
    description = (
        "Access Reminders.app on macOS. Supported actions: list_lists, list_reminders, "
        "create_reminder, update_reminder, complete_reminder."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_lists",
                    "list_reminders",
                    "create_reminder",
                    "update_reminder",
                    "complete_reminder",
                ],
                "description": "Reminders action to execute.",
            },
            "list_name": {"type": "string", "description": "Visible Reminders list name."},
            "include_completed": {
                "type": "boolean",
                "description": "Include completed reminders in list_reminders.",
                "default": False,
            },
            "query": {"type": "string", "description": "Optional substring filter for list_reminders."},
            "limit": {"type": "integer", "description": "Maximum list_reminders results.", "default": 100},
            "reminder_id": {
                "type": "string",
                "description": "Opaque reminder id returned by prior tool calls.",
            },
            "title": {"type": "string", "description": "Reminder title."},
            "notes": {"type": "string", "description": "Reminder notes/body."},
            "due_on": {"type": "string", "description": "Date-only due date in YYYY-MM-DD format."},
            "due_at": {
                "type": "string",
                "description": "Timed due date in ISO 8601 format with an explicit timezone offset.",
            },
            "clear_due": {
                "type": "boolean",
                "description": "Clear any existing due date on update_reminder.",
                "default": False,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, helper: MacOSHelper) -> None:
        self.helper = helper

    def execute(self, context, **kwargs) -> ToolResult:
        del context
        try:
            action = _require_string(kwargs, "action")
            arguments = self._build_arguments(action, kwargs)
            data = self.helper.execute(app="reminders", action=action, arguments=arguments)
            return ToolResult(success=True, data=data)
        except (ValueError, MacOSHelperError) as exc:
            return ToolResult(success=False, error=str(exc))

    def _build_arguments(self, action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if action == "list_lists":
            return {}
        if action == "list_reminders":
            return {
                "list_name": _optional_string(kwargs, "list_name"),
                "include_completed": _require_bool(kwargs, "include_completed", default=False),
                "query": _optional_string(kwargs, "query"),
                "limit": _require_int(kwargs, "limit", default=100),
            }
        if action == "create_reminder":
            due_on = _optional_date_only(kwargs, "due_on")
            due_at = _optional_iso_datetime(kwargs, "due_at")
            self._validate_due_inputs(due_on=due_on, due_at=due_at, clear_due=False)
            return {
                "list_name": _require_string(kwargs, "list_name"),
                "title": _require_string(kwargs, "title"),
                "notes": _optional_string(kwargs, "notes"),
                "due_on": due_on,
                "due_at": due_at,
            }
        if action == "update_reminder":
            due_on = _optional_date_only(kwargs, "due_on")
            due_at = _optional_iso_datetime(kwargs, "due_at")
            clear_due = _require_bool(kwargs, "clear_due", default=False)
            self._validate_due_inputs(due_on=due_on, due_at=due_at, clear_due=clear_due)
            updates = {
                "title": _optional_string(kwargs, "title"),
                "notes": _optional_string(kwargs, "notes"),
                "due_on": due_on,
                "due_at": due_at,
                "clear_due": clear_due,
            }
            if not any(
                value is not None
                for key, value in updates.items()
                if key != "clear_due"
            ) and not clear_due:
                raise ValueError("update_reminder requires at least one updated field")
            return {"reminder_id": _require_string(kwargs, "reminder_id"), **updates}
        if action == "complete_reminder":
            return {"reminder_id": _require_string(kwargs, "reminder_id")}
        raise ValueError(f"Unsupported reminders action: {action}")

    @staticmethod
    def _validate_due_inputs(
        *,
        due_on: str | None,
        due_at: str | None,
        clear_due: bool,
    ) -> None:
        if due_on is not None and due_at is not None:
            raise ValueError("due_on and due_at are mutually exclusive")
        if clear_due and (due_on is not None or due_at is not None):
            raise ValueError("clear_due cannot be combined with due_on or due_at")


class MessagesActionTool(Tool):
    """Bounded Messages.app helper tool."""

    name = "messages_action"
    description = (
        "Access Messages.app on macOS. Supported actions: list_chats, read_recent_messages."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_chats", "read_recent_messages"],
                "description": "Messages action to execute.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum list_chats or read_recent_messages results.",
                "default": 100,
            },
            "chat_id": {"type": "string", "description": "Opaque chat id returned by list_chats."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, helper: MacOSHelper) -> None:
        self.helper = helper

    def execute(self, context, **kwargs) -> ToolResult:
        del context
        try:
            action = _require_string(kwargs, "action")
            arguments = self._build_arguments(action, kwargs)
            data = self.helper.execute(app="messages", action=action, arguments=arguments)
            return ToolResult(success=True, data=data)
        except (ValueError, MacOSHelperError) as exc:
            return ToolResult(success=False, error=str(exc))

    def _build_arguments(self, action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if action == "list_chats":
            return {"limit": _require_int(kwargs, "limit", default=100)}
        if action == "read_recent_messages":
            return {
                "chat_id": _require_string(kwargs, "chat_id"),
                "limit": _require_int(kwargs, "limit", default=20),
            }
        raise ValueError(f"Unsupported messages action: {action}")
