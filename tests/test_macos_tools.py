"""Tests for macOS helper-backed tools."""

import json
import subprocess
import sys

import pytest

from src.config import Config
from src.context import Context
from src.skills import SkillManager
from src.tools import ToolProfile, build_tool_registry, build_tool_registry_with_report
from src.tools.macos import (
    CalendarActionTool,
    FinderActionTool,
    MacOSHelper,
    MacOSHelperError,
    MacOSOutputError,
    MacOSPermissionError,
    MessagesActionTool,
    NotesActionTool,
    RemindersActionTool,
)


class StubHelper:
    """Simple helper stub for tool validation tests."""

    def __init__(self, result=None):
        self.result = result if result is not None else {"ok": True}
        self.calls = []

    def execute(self, *, app, action, arguments):
        self.calls.append({"app": app, "action": action, "arguments": arguments})
        return self.result


def test_macos_helper_returns_structured_success(monkeypatch):
    """The helper should decode successful JSON responses."""
    helper = MacOSHelper(timeout_seconds=5)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps({"ok": True, "data": {"items": []}}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    data = helper.execute(app="finder", action="list_items", arguments={"path": "/tmp"})

    assert data == {"items": []}


def test_macos_helper_maps_timeout(monkeypatch):
    """Timeouts should become a stable helper error."""
    helper = MacOSHelper(timeout_seconds=5)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(MacOSHelperError, match="timed out"):
        helper.execute(app="finder", action="list_items", arguments={"path": "/tmp"})


def test_macos_helper_rejects_malformed_json(monkeypatch):
    """Malformed helper stdout should fail clearly."""
    helper = MacOSHelper(timeout_seconds=5)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="not-json",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(MacOSOutputError, match="malformed JSON"):
        helper.execute(app="notes", action="list_notes", arguments={})


def test_macos_helper_maps_permission_denied(monkeypatch):
    """Structured permission failures should become actionable permission errors."""
    helper = MacOSHelper(timeout_seconds=5)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "permission_denied",
                        "message": "Not authorized to send Apple events",
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(MacOSPermissionError, match="Automation permission denied"):
        helper.execute(app="calendar", action="list_calendars", arguments={})


def test_finder_tool_validates_required_arguments(temp_dir):
    """Finder actions should reject missing required arguments before invoking the helper."""
    helper = StubHelper()
    tool = FinderActionTool(helper)

    result = tool.execute(Context.create(cwd=str(temp_dir)), action="rename_item", path="foo.txt")

    assert result.success is False
    assert result.error == "new_name is required"
    assert helper.calls == []


def test_calendar_tool_validates_update_event_fields():
    """Calendar updates should require at least one changed field."""
    helper = StubHelper()
    tool = CalendarActionTool(helper)

    result = tool.execute(Context.create(cwd="."), action="update_event", event_id="evt-1")

    assert result.success is False
    assert result.error == "update_event requires at least one updated field"
    assert helper.calls == []


def test_notes_tool_validates_body_mode():
    """Notes updates should enforce the bounded body_mode enum."""
    helper = StubHelper()
    tool = NotesActionTool(helper)

    result = tool.execute(
        Context.create(cwd="."),
        action="update_note",
        note_id="note-1",
        body_text="append me",
        body_mode="merge",
    )

    assert result.success is False
    assert result.error == "body_mode must be replace or append"
    assert helper.calls == []


def test_reminders_tool_rejects_conflicting_due_fields():
    """Reminders should reject due_on and due_at in the same request."""
    helper = StubHelper()
    tool = RemindersActionTool(helper)

    result = tool.execute(
        Context.create(cwd="."),
        action="create_reminder",
        list_name="Inbox",
        title="Pay rent",
        due_on="2026-03-10",
        due_at="2026-03-10T09:00:00-08:00",
    )

    assert result.success is False
    assert result.error == "due_on and due_at are mutually exclusive"
    assert helper.calls == []


def test_reminders_tool_rejects_clear_due_with_new_due_value():
    """Reminder updates should not mix clear_due with a replacement due date."""
    helper = StubHelper()
    tool = RemindersActionTool(helper)

    result = tool.execute(
        Context.create(cwd="."),
        action="update_reminder",
        reminder_id="rem-1",
        clear_due=True,
        due_on="2026-03-10",
    )

    assert result.success is False
    assert result.error == "clear_due cannot be combined with due_on or due_at"
    assert helper.calls == []


def test_messages_tool_requires_chat_id_for_history_reads():
    """Messages reads should require a chat id before invoking the helper."""
    helper = StubHelper()
    tool = MessagesActionTool(helper)

    result = tool.execute(Context.create(cwd="."), action="read_recent_messages")

    assert result.success is False
    assert result.error == "chat_id is required"
    assert helper.calls == []


def test_build_tool_registry_registers_macos_tools_only_for_enabled_darwin_build(temp_dir, monkeypatch):
    """macOS helper tools should register only in main build mode on Darwin."""
    runtime_config = Config(
        {
            "macos_tools": {
                "enabled": True,
                "enable_finder": True,
                "enable_calendar": True,
                "enable_notes": True,
                "enable_reminders": True,
                "enable_messages": True,
            }
        }
    )
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )
    skill_manager.discover()

    monkeypatch.setattr(sys, "platform", "darwin")

    build_registry = build_tool_registry(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )
    subagent_registry = build_tool_registry(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD_SUBAGENT,
        runtime_config=runtime_config,
    )

    assert {
        "finder_action",
        "calendar_action",
        "notes_action",
        "reminders_action",
        "messages_action",
    } <= set(build_registry.list_tools())
    assert "finder_action" not in subagent_registry.list_tools()
    assert "calendar_action" not in subagent_registry.list_tools()
    assert "notes_action" not in subagent_registry.list_tools()
    assert "reminders_action" not in subagent_registry.list_tools()
    assert "messages_action" not in subagent_registry.list_tools()


def test_build_tool_registry_report_marks_macos_disabled(temp_dir, monkeypatch):
    """Report output should explain when macOS tools are disabled in config."""
    runtime_config = Config({"macos_tools": {"enabled": False}})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )
    skill_manager.discover()

    monkeypatch.setattr(sys, "platform", "darwin")

    registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert "finder_action" not in registry.list_tools()
    assert decisions["macos_tools"] == "skipped: macos_tools.enabled is false"
    assert decisions["finder_action"] == "skipped: macos_tools.enabled is false"
    assert decisions["calendar_action"] == "skipped: macos_tools.enabled is false"
    assert decisions["notes_action"] == "skipped: macos_tools.enabled is false"
    assert decisions["reminders_action"] == "skipped: macos_tools.enabled is false"
    assert decisions["messages_action"] == "skipped: macos_tools.enabled is false"


def test_build_tool_registry_report_marks_macos_registered_on_darwin_by_default(temp_dir, monkeypatch):
    """Report output should mark macOS tools registered by default on Darwin."""
    monkeypatch.delenv("MACOS_TOOLS_ENABLED", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_FINDER", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_CALENDAR", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_NOTES", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_REMINDERS", raising=False)
    monkeypatch.delenv("MACOS_TOOLS_ENABLE_MESSAGES", raising=False)
    runtime_config = Config({})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )
    skill_manager.discover()

    monkeypatch.setattr(sys, "platform", "darwin")

    registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert {
        "finder_action",
        "calendar_action",
        "notes_action",
        "reminders_action",
        "messages_action",
    } <= set(registry.list_tools())
    assert decisions["macos_tools"] == "registered"
    assert decisions["finder_action"] == "registered"
    assert decisions["calendar_action"] == "registered"
    assert decisions["notes_action"] == "registered"
    assert decisions["reminders_action"] == "registered"
    assert decisions["messages_action"] == "registered"


def test_build_tool_registry_report_marks_only_disabled_app_tool_skipped(temp_dir, monkeypatch):
    """Per-app config disables should only skip the requested macOS tool."""
    runtime_config = Config({"macos_tools": {"enable_messages": False}})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )
    skill_manager.discover()

    monkeypatch.setattr(sys, "platform", "darwin")

    registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert "finder_action" in registry.list_tools()
    assert "notes_action" in registry.list_tools()
    assert "calendar_action" in registry.list_tools()
    assert "reminders_action" in registry.list_tools()
    assert "messages_action" not in registry.list_tools()
    assert decisions["macos_tools"] == "registered"
    assert decisions["finder_action"] == "registered"
    assert decisions["calendar_action"] == "registered"
    assert decisions["notes_action"] == "registered"
    assert decisions["reminders_action"] == "registered"
    assert decisions["messages_action"] == "skipped: macos_tools.enable_messages is false"


def test_build_tool_registry_report_marks_platform_skip_for_macos(temp_dir, monkeypatch):
    """Report output should explain when macOS tools are blocked by platform."""
    runtime_config = Config({"macos_tools": {"enabled": True}})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="linux",
    )
    skill_manager.discover()

    monkeypatch.setattr(sys, "platform", "linux")

    registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert "finder_action" not in registry.list_tools()
    assert decisions["macos_tools"] == "skipped: platform is linux, requires darwin"
    assert decisions["finder_action"] == "skipped: platform is linux, requires darwin"
    assert decisions["calendar_action"] == "skipped: platform is linux, requires darwin"
    assert decisions["notes_action"] == "skipped: platform is linux, requires darwin"
    assert decisions["reminders_action"] == "skipped: platform is linux, requires darwin"
    assert decisions["messages_action"] == "skipped: platform is linux, requires darwin"


def test_build_tool_registry_report_marks_profile_skip_for_macos(temp_dir, monkeypatch):
    """Report output should explain when plan-mode profiles exclude macOS tools."""
    runtime_config = Config({"macos_tools": {"enabled": True}})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="darwin",
    )
    skill_manager.discover()

    monkeypatch.setattr(sys, "platform", "darwin")

    registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.PLAN_MAIN,
        runtime_config=runtime_config,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert "finder_action" not in registry.list_tools()
    assert decisions["macos_tools"] == "skipped: tool profile is plan_main, requires build"
    assert decisions["finder_action"] == "skipped: tool profile is plan_main, requires build"
    assert decisions["calendar_action"] == "skipped: tool profile is plan_main, requires build"
    assert decisions["notes_action"] == "skipped: tool profile is plan_main, requires build"
    assert decisions["reminders_action"] == "skipped: tool profile is plan_main, requires build"
    assert decisions["messages_action"] == "skipped: tool profile is plan_main, requires build"


def test_build_tool_registry_report_prefers_disabled_subagents_reason(temp_dir):
    """Subagent skip reasons should report disabled config before internal include flags."""
    runtime_config = Config({"subagents": {"enabled": False}})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
    )
    skill_manager.discover()

    _registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
        include_subagent_tool=False,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert decisions["subagents"] == "skipped: subagents are disabled"
