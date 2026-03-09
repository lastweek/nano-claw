---
name: macos-reminders
description: Use this when the task needs Apple Reminders.app on macOS for listing lists, finding reminders, or creating/updating/completing reminders.
metadata:
  short-description: Apple Reminders access on macOS
  requires:
    os:
      - darwin
    config:
      - macos_tools.enabled
      - macos_tools.enable_reminders
---

Use `reminders_action` for Reminders.app tasks on macOS.

Supported actions:
- `list_lists`
- `list_reminders`
- `create_reminder`
- `update_reminder`
- `complete_reminder`

Date rules:
- Use `due_on` for all-day reminders in `YYYY-MM-DD` format.
- Use `due_at` for timed reminders in ISO 8601 format with an explicit timezone offset.
- Never send both `due_on` and `due_at` in the same request.
- Use `clear_due=true` on `update_reminder` only when the user explicitly wants to remove an existing due date.

Workflow:
1. Start with `list_lists` if the target reminder list is unclear.
2. Use `list_reminders` before updating or completing an existing reminder so you capture the correct `reminder_id`.
3. Reuse the returned `reminder_id` for `update_reminder` or `complete_reminder`.

Field guidance:
- `title` is the visible reminder name.
- `notes` maps to the reminder body/notes field.
- `include_completed` should stay `false` unless completed reminders are explicitly relevant.

Do not assume delete, list moves, subtasks, recurrence, tags, or priority support in v1.
