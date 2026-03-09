---
name: macos-notes
description: Use this when the task needs Apple Notes.app on macOS for listing, reading, creating, or updating notes.
metadata:
  short-description: Apple Notes access on macOS
  requires:
    os:
      - darwin
    config:
      - macos_tools.enabled
      - macos_tools.enable_notes
---

Use `notes_action` for Notes.app tasks on macOS.

Supported actions:
- `list_notes`
- `read_note`
- `create_note`
- `update_note`

Workflow:
1. Use `list_notes` to find the target note and capture its `note_id`.
2. Use `read_note` when the full body is needed before editing.
3. Use `create_note` with `folder_name`, `title`, and `body_text`.
4. Use `update_note` with `body_mode="append"` only when the user explicitly wants additive updates.

Field guidance:
- `folder_name` is the visible Notes folder name.
- `body_text` is plain text input.
- `note_id` must come from a previous tool result.

Do not assume delete, move, rich-text formatting, or attachment support in v1.
