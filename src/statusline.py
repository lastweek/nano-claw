"""Shared CLI status line helpers for idle and running states."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from prompt_toolkit.formatted_text import HTML
from rich.text import Text

from src.context import Context


SEPARATOR_STYLE = "#6b7280"
LABEL_STYLE = "#6b7280"
BUILD_MODE_STYLE = "#94a3b8"
PLAN_MODE_STYLE = "#b7a089"
VIEW_VALUE_STYLE = "#9ca3af"
TIP_STYLE = "#7c7f89"
CONTRACT_STYLE = "#8fa58f"


@dataclass(frozen=True)
class StatusLineState:
    """Compact session state shown in the CLI status line."""

    session_mode: str
    view_mode: str
    plan_state: str
    contract_active: bool
    tip_text: str


def build_statusline_state(
    session_context: Context,
    *,
    view_mode: str,
) -> StatusLineState:
    """Build the compact state reflected in the CLI status line."""
    current_plan = session_context.get_current_plan()
    if current_plan is None:
        plan_state = "none"
    elif current_plan.status == "ready_for_review":
        plan_state = "ready"
    else:
        plan_state = current_plan.status

    return StatusLineState(
        session_mode=session_context.get_session_mode().upper(),
        view_mode=view_mode,
        plan_state=plan_state,
        contract_active=session_context.get_active_approved_plan() is not None,
        tip_text=_build_tip_text(
            session_mode=session_context.get_session_mode(),
            plan_state=plan_state,
            contract_active=session_context.get_active_approved_plan() is not None,
        ),
    )


def build_statusline_text(
    session_context: Context,
    *,
    view_mode: str,
) -> str:
    """Format the shared CLI status line as plain text."""
    state = build_statusline_state(
        session_context,
        view_mode=view_mode,
    )
    parts = [
        state.session_mode,
        f"view:{state.view_mode}",
        f"plan:{state.plan_state}",
    ]
    if state.contract_active:
        parts.append("contract:on")
    if state.tip_text:
        parts.append(f"tip:{state.tip_text}")
    return " | ".join(parts)


def build_rich_statusline(
    session_context: Context,
    *,
    view_mode: str,
) -> Text:
    """Build a Rich-rendered status line for the running live transcript."""
    state = build_statusline_state(
        session_context,
        view_mode=view_mode,
    )

    mode_style = BUILD_MODE_STYLE if state.session_mode == "BUILD" else PLAN_MODE_STYLE
    plan_style = _plan_value_style(state.plan_state)

    text = Text()
    text.append(state.session_mode, style=mode_style)
    text.append(" | ", style=SEPARATOR_STYLE)
    text.append("view:", style=LABEL_STYLE)
    text.append(state.view_mode, style=VIEW_VALUE_STYLE)
    text.append(" | ", style=SEPARATOR_STYLE)
    text.append("plan:", style=LABEL_STYLE)
    text.append(state.plan_state, style=plan_style)
    if state.contract_active:
        text.append(" | ", style=SEPARATOR_STYLE)
        text.append("contract:on", style=CONTRACT_STYLE)
    if state.tip_text:
        text.append(" | ", style=SEPARATOR_STYLE)
        text.append("tip:", style=LABEL_STYLE)
        text.append(state.tip_text, style=f"italic {TIP_STYLE}")
    return text


def build_prompt_toolbar(
    session_context: Context,
    *,
    view_mode: str,
) -> HTML:
    """Build a prompt-toolkit bottom toolbar for idle input state."""
    state = build_statusline_state(
        session_context,
        view_mode=view_mode,
    )
    mode_color = BUILD_MODE_STYLE if state.session_mode == "BUILD" else PLAN_MODE_STYLE
    plan_color = _prompt_plan_value_color(state.plan_state)
    contract_html = ""
    if state.contract_active:
        contract_html = (
            f"<style fg='{SEPARATOR_STYLE}'> | </style>"
            f"<style fg='{CONTRACT_STYLE}'>contract:on</style>"
        )
    tip_html = ""
    if state.tip_text:
        tip_html = (
            f"<style fg='{SEPARATOR_STYLE}'> | </style>"
            f"<style fg='{LABEL_STYLE}'>tip:</style><style fg='{TIP_STYLE}'>{escape(state.tip_text)}</style>"
        )

    html = (
        f"<style fg='{mode_color}'>{escape(state.session_mode)}</style>"
        f"<style fg='{SEPARATOR_STYLE}'> | </style>"
        f"<style fg='{LABEL_STYLE}'>view:</style><style fg='{VIEW_VALUE_STYLE}'>{escape(state.view_mode)}</style>"
        f"<style fg='{SEPARATOR_STYLE}'> | </style>"
        f"<style fg='{LABEL_STYLE}'>plan:</style><style fg='{plan_color}'>{escape(state.plan_state)}</style>"
        f"{contract_html}"
        f"{tip_html}"
    )
    return HTML(html)


def _plan_value_style(plan_state: str) -> str:
    """Return the Rich style for one plan-state value."""
    if plan_state in {"ready", "draft"}:
        return "#b39a7a"
    if plan_state in {"approved", "executing", "completed"}:
        return "#8fa58f"
    if plan_state == "rejected":
        return "#b18b8b"
    return "#6b7280"


def _prompt_plan_value_color(plan_state: str) -> str:
    """Return the prompt-toolkit color for one plan-state value."""
    if plan_state in {"ready", "draft"}:
        return "#b39a7a"
    if plan_state in {"approved", "executing", "completed"}:
        return "#8fa58f"
    if plan_state == "rejected":
        return "#b18b8b"
    return "#6b7280"


def _build_tip_text(
    *,
    session_mode: str,
    plan_state: str,
    contract_active: bool,
) -> str:
    """Return a short contextual tip for the status line."""
    if session_mode == "plan":
        if plan_state == "ready":
            return "Shift+Tab build mode | /plan apply"
        return "Shift+Tab build mode | /plan show"

    if contract_active:
        return "Shift+Tab plan mode | /plan clear"

    if plan_state == "ready":
        return "Shift+Tab plan mode | /plan apply"

    return "Shift+Tab plan mode"
