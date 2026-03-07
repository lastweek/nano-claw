---
name: macos-calendar
description: Use this when the task needs Apple Calendar.app on macOS for listing calendars, inspecting events, or creating/updating timed events.
metadata:
  short-description: Apple Calendar access on macOS
  requires:
    os:
      - darwin
    config:
      - macos_tools.enabled
      - macos_tools.enable_calendar
---

Use `calendar_action` for Calendar.app tasks on macOS.

Supported actions:
- `list_calendars`
- `list_events`
- `create_event`
- `update_event`

Time rules:
- Always use ISO 8601 datetimes with explicit timezone offsets.
- Treat v1 events as timed events only.
- When creating events, ensure `end_at` is later than `start_at`.

Workflow:
1. Start with `list_calendars` if the target calendar is unclear.
2. Use `list_events` with a bounded time window before updating an event.
3. Reuse the returned `event_id` for `update_event`.

Field guidance:
- `title` is the event title.
- `notes` is event body text.
- `location` is optional.

Do not assume recurring events, attendees, or attachments are supported in v1.
