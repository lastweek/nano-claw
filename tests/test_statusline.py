"""Tests for shared CLI status line helpers."""

from rich.console import Console

from src.context import Context, SessionPlan
from src.statusline import build_rich_statusline, build_statusline_text


def test_build_statusline_text_for_default_build_session():
    """Status line should reflect the default build session state."""
    context = Context.create()

    text = build_statusline_text(
        context,
        view_mode="simple",
    )

    assert text == "BUILD | view:simple | plan:none | tip:Shift+Tab plan mode"


def test_build_statusline_text_reflects_ready_plan_and_active_contract():
    """Status line should compact ready-for-review and show active contracts."""
    context = Context.create()
    context.set_session_mode("plan")
    context.set_current_plan(
        SessionPlan(
            plan_id="plan-1",
            status="ready_for_review",
            task="Design plan mode",
            file_path=".babyclaw/plans/session.md",
            content="# Plan",
            summary="Ready",
            created_at="2026-03-03T00:00:00Z",
            updated_at="2026-03-03T00:00:00Z",
            approved_at="2026-03-03T00:00:00Z",
        )
    )
    context.active_approved_plan_id = "plan-1"

    text = build_statusline_text(
        context,
        view_mode="verbose",
    )

    assert (
        text
        == "PLAN | view:verbose | plan:ready | contract:on | "
        "tip:Shift+Tab build mode | /plan apply"
    )


def test_build_rich_statusline_exports_plain_text():
    """The Rich footer should export the same compact state text."""
    context = Context.create()
    console = Console(record=True, force_terminal=False, width=120)

    console.print(
        build_rich_statusline(
            context,
            view_mode="simple",
        )
    )

    assert (
        console.export_text().strip()
        == "BUILD | view:simple | plan:none | tip:Shift+Tab plan mode"
    )


def test_build_statusline_text_reflects_active_contract_in_build_mode():
    """An active approved plan in build mode should point to show/clear actions."""
    context = Context.create()
    context.set_current_plan(
        SessionPlan(
            plan_id="plan-2",
            status="executing",
            task="Implement feature",
            file_path=".babyclaw/plans/session.md",
            content="# Plan",
            summary="Approved",
            created_at="2026-03-03T00:00:00Z",
            updated_at="2026-03-03T00:00:00Z",
            approved_at="2026-03-03T00:00:00Z",
        )
    )
    context.active_approved_plan_id = "plan-2"

    text = build_statusline_text(
        context,
        view_mode="simple",
    )

    assert (
        text
        == "BUILD | view:simple | plan:executing | contract:on | "
        "tip:Shift+Tab plan mode | /plan clear"
    )
