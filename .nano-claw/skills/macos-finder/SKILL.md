---
name: macos-finder
description: Use this when the task needs Finder-style file browsing or local file reveal/open actions on macOS.
metadata:
  short-description: Finder file navigation on macOS
  requires:
    os:
      - darwin
    config:
      - macos_tools.enabled
      - macos_tools.enable_finder
---

Use `finder_action` for Finder-oriented local file tasks on macOS.

Preferred actions:
- `list_items` to inspect a directory before taking follow-up actions
- `reveal_item` to show a file or folder in Finder
- `open_item` to open the selected file or folder with the system default behavior
- `create_folder` to create a new folder
- `rename_item` to rename an existing item

Path rules:
- Pass absolute paths when you already have them.
- Relative paths are resolved from the current session working directory.
- For `list_items`, provide a directory path and keep `limit` bounded.

Workflow:
1. List the target directory if the user has not identified the exact item yet.
2. Confirm the target path from the tool result.
3. Run the narrower action (`reveal_item`, `open_item`, `create_folder`, or `rename_item`).

Do not use this skill for file content edits. Use normal repo file tools for that.
