---
name: macos-messages
description: Use this when the task needs Apple Messages.app on macOS for listing chats or reading recent messages from a chat that exposes scriptable history.
metadata:
  short-description: Apple Messages access on macOS
  requires:
    os:
      - darwin
    config:
      - macos_tools.enabled
      - macos_tools.enable_messages
---

Use `messages_action` for Messages.app tasks on macOS.

Supported actions:
- `list_chats`
- `read_recent_messages`

Workflow:
1. Use `list_chats` first to identify the target conversation and capture its `chat_id`.
2. Use `read_recent_messages` only with a `chat_id` returned by the tool.
3. Keep `limit` bounded and narrow the request to the specific chat the user asked about.

Caveat:
- Messages scripting does not expose readable history for every chat type.
- If `read_recent_messages` returns an unsupported-history error, report that limitation plainly instead of retrying with UI scripting or broader access.

Do not assume send, delete, create-chat, attachment, or global search support in v1.
