# nano-claw

nano-claw is a terminal-first coding agent for working directly inside an existing repository. It combines an OpenAI-compatible LLM client with local tools, slash commands, skills, MCP integrations, delegated subagents, and per-session logs so you can see exactly what happened during each turn.

It is built for practical repo work rather than chat demos: inspect files, edit code, run commands, load focused instructions only when needed, hand off isolated subtasks, and keep long sessions usable with context compaction.

It also includes a small localhost HTTP wrapper so you can drive the same repo-scoped runtime from a browser without adding auth, multi-user state, or remote deployment complexity.

## Why nano-claw

- Work where the code already is: your shell, your repo, your files
- Keep the agent observable with a live activity feed and structured logs
- Extend behavior with local skills and MCP servers instead of giant prompts
- Delegate parallel subtasks without mixing child work into the main context
- Stay productive in longer sessions with context inspection and compaction

## What You Get

- Built-in tools for repo work, self-extension, and public-web reading: `read_file`, `write_file`, `run_command`, `load_skill`, `run_subagent`, `find_capabilities`, `request_capability`, `fetch_url`, `read_webpage`, and `extract_page_links`
- Optional macOS helper tools for Finder, Calendar.app, and Notes.app when enabled
- Streaming terminal UX with live activity updates while the model is thinking
- Optional local HTTP/SSE server with a tiny browser UI for session-based turns
- Slash commands for tools, skills, MCP servers, plans, subagents, and context usage
- Local `SKILL.md` bundles with discovery, pinning, and on-demand loading
- MCP support so external tools appear beside built-in tools
- Subagents with isolated contexts, nested logs, and bounded concurrency
- Per-session logging under `logs/` with human-readable and structured outputs
- Planning workflow via `/plan`
- Automatic and manual context compaction for long-running sessions

## Quickstart

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
git config core.hooksPath .githooks
```

The git hook path enables the local secret guard so obvious credentials do not get committed by accident.

### 2. Configure

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

`config.yaml` is the main project config. Use `.env` for secrets and machine-local overrides. Environment variables override values from `config.yaml`.

Minimal OpenAI-compatible configuration:

```yaml
llm:
  provider: custom
  model: your-model-name
  base_url: https://api.example.com/v1
  api_key: your-api-key
```

You can also use `openai`, `azure`, or `ollama` provider modes.

### 3. Run

```bash
python -m src.main
```

Alternative entrypoint:

```bash
python src/main.py
```

Start the local HTTP wrapper instead:

```bash
python -m src.main serve
```

`serve` prints the local access points on startup, including:

- chat UI: `http://127.0.0.1:8765/`
- admin UI: `http://127.0.0.1:8765/admin`
- health: `http://127.0.0.1:8765/api/v1/health`

If you bind HTTP mode to a non-loopback host, nano-claw prints a warning because the chat and admin surfaces have no auth in v1.

## What It Looks Like

```text
You > explain how the logging system works

Agent >
  • LLM call 1 requested 2 tools
  • Tool finished: run_command(cmd='rg "SessionLogger" -n src') (0.03s)
  • Tool finished: read_file(file_path='src/logger.py') (0.00s)
  • LLM call 2 produced final answer

nano-claw writes one session directory per CLI run, with session.json for
metadata, llm.log for the execution timeline, and events.jsonl for structured
events.

1234 prompt tokens • 87 completion tokens • 2.14s • TTFT 0.61s • glm-5 (custom)
```

## Core Ideas

### Tools

nano-claw uses the same tool-calling loop for built-in tools and MCP-provided tools. The default repo-workflow set covers reading files, writing files, running shell commands, loading skills, delegating child tasks, and reading public webpages.

### Skills

Skills are local instruction bundles stored in `SKILL.md`. nano-claw keeps a compact skill catalog in context and loads full skill bodies only when they are pinned, explicitly requested with `$skill-name`, or loaded through the `load_skill` tool.

Skills can also declare runtime requirements in frontmatter. For example, repo-local macOS skills can require `darwin` plus `macos_tools.*` config flags, which keeps them visible in `/skill` while hiding them from the prompt catalog unless the local helper is actually available or has been explicitly disabled.

Discovery roots:

- `.nano-claw/skills`
- `~/.nano-claw/skills`

### MCP

MCP servers are configured in `config.yaml` and initialized at startup. Their tools are registered into the same tool system as built-in tools, so the model can use them through the same request loop and you can inspect them with `/mcp`.

The intended internet workflow is hybrid: core handles URL fetching and page reading, while search providers come from MCP. That keeps the core small and lets you swap search backends without changing nano-claw itself.

### Live Self-Extension

When the current session is missing a tool or skill, the agent can inspect available capabilities and raise a structured request instead of guessing. The intended workflow is:

- `find_capabilities` to search active tools, loadable skills, on-disk extension bundles that need reload, and installable extension catalog packages
- `request_capability` to record the missing capability for the current session
- user action such as `/extension install <catalog>:<package>` or `/runtime reload`
- `refresh_runtime_capabilities` or `/runtime reload` so the session picks up the new capability

### Subagents

Subagents let the main agent delegate isolated repo tasks to fresh child agents. They do not inherit the full conversation history, they write nested logs, and they run with bounded parallelism.

### Context Compaction

Long sessions can compact older turns into a rolling summary while keeping recent turns in raw form. You can inspect the current estimate with `/context` and manage compaction with `/compact`.

### Logging

Every CLI or HTTP session gets a readable session directory under `~/.nano-claw/sessions/` by default:

- `session.json` for session metadata and aggregate counts
- `llm.log` for the full human-readable execution timeline
- `events.jsonl` for the structured event stream
- `artifacts/` for spilled large payloads
- `MEMORY.md` for curated session memory
- `daily/` for append-only daily memory notes
- `memory-settings.json` and `memory-audit.jsonl` for memory controls and audit events
- `subagents/` for nested child-agent logs

Convenience symlinks are also maintained at `~/.nano-claw/sessions/latest-session` and `~/.nano-claw/sessions/latest.log`.

## Common Commands

- `/help` to list commands
- `/capability` to inspect, dismiss, and resolve missing-capability requests
- `/tool` to inspect available tools
- `/skill` to list, pin, clear, inspect, and reload skills
- `/mcp` to inspect configured MCP servers and tools
- `/context` to estimate the next-call baseline context payload
- `/compact` to inspect or trigger compaction behavior
- `/subagent` to inspect or run delegated child tasks
- `/plan` to enter planning mode, draft a plan, and apply it

Each command also supports built-in help such as `/command help`, `/command --help`, and `/command -h`.

## Configuration

nano-claw uses three configuration sources:

- `config.yaml` for primary local configuration
- `.env` for secrets and machine-local values
- environment variables for explicit overrides

Useful settings to know early:

- `ui.enable_streaming`
- `agent.max_iterations`
- `server.host`
- `server.port`
- `server.db_path`
- `logging.enabled`
- `logging.async_mode`
- `logging.log_dir`
- `subagents.max_parallel`
- `subagents.max_per_turn`
- `context.auto_compact`
- `plan.enabled`
- `macos_tools.enabled`
- `web_tools.enabled`
- `extensions.enabled`

`logging.async_mode` routes session-log writes through a background transport while keeping the same on-disk log format. `subagents.max_parallel` caps concurrent child-agent threads independently from the per-turn delegation limit.
By default, `server.db_path` points to `~/.nano-claw/state.db`.
By default, `logging.log_dir` points to `~/.nano-claw/sessions`, and each session keeps its logs and memory files inside the same per-session directory.

Example macOS helper config:

```yaml
macos_tools:
  enabled: true
  timeout_seconds: 10
  enable_finder: true
  enable_calendar: true
  enable_notes: true
  enable_reminders: true
  enable_messages: true
```

On macOS, nano-claw enables bounded `finder_action`, `calendar_action`, `notes_action`, `reminders_action`, and `messages_action` tools by default. Set `macos_tools.enabled: false` to opt out. Unsupported platforms skip these tools at startup and report the platform reason in `TOOL_DEBUG=1` output. The first run on macOS may require granting Automation access in macOS System Settings. `messages_action.read_recent_messages` is best-effort because some chat objects do not expose readable history through the Messages scripting surface.

Example public-web tool config:

```yaml
web_tools:
  enabled: true
  timeout_seconds: 15
  max_response_bytes: 2000000
  max_content_chars: 20000
  allow_private_networks: false
  enable_fetch_url: true
  enable_read_webpage: true
  enable_extract_page_links: true
```

`fetch_url`, `read_webpage`, and `extract_page_links` are enabled by default in normal build sessions, build subagents, and main planning sessions. They only allow public `http` and `https` targets unless `web_tools.allow_private_networks` is set to `true`. Search is not built into core; add an MCP search provider and use that to find URLs before reading them with the built-in web tools.

Example runtime extension config:

```yaml
extensions:
  enabled: true
  user_root: ~/.nano-claw/extensions
  repo_root: .nano-claw/extensions
  runner_timeout_seconds: 60
  install_timeout_seconds: 30
  catalogs: []
```

`extensions.enabled` turns on live-discoverable out-of-process tool bundles under the repo-local and user-global extension roots. Add new bundles, then run `/runtime reload`, `/extension reload`, or `refresh_runtime_capabilities` to activate them without restarting the process. Curated remote installs are explicit-user actions through `/extension install <catalog>:<package>` or `POST /api/v1/admin/extensions/install`; search/install approval is not delegated to normal tool calls.

If the agent discovers it needs a capability that is not active yet, it should use `find_capabilities` and `request_capability` instead of inventing tool names. Exact catalog matches should be reported with the concrete `/extension install ...` and `/runtime reload` steps.

Example MCP config:

```yaml
mcp:
  servers:
    - name: deepwiki
      url: https://mcp.deepwiki.com/mcp
      enabled: true
      timeout: 30
```

See [config.yaml.example](config.yaml.example) for the full example file.

## HTTP Mode

The optional HTTP wrapper is intentionally local and minimal:

- One daemon process started inside one repo
- One machine-global SQLite database for persisted sessions and turns
- One long-running main `Agent` per active session
- One dedicated worker thread per session runtime
- No auth, approvals, or multi-user features
- SSE streaming for live output and turn status
- Tiny static UI served from the same process

By default the HTTP runtime stores state in `~/.nano-claw/state.db`, shared across repos on the same machine. On first startup with the default paths, nano-claw also moves an existing repo-local `.nano-claw/state.db`, `logs/`, and `.nano-claw/memory/` there when the corresponding global targets are still empty.

Key endpoints:

- `GET /api/v1/health`
- `GET /api/v1/sessions`
- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `DELETE /api/v1/sessions/{session_id}`
- `POST /api/v1/sessions/{session_id}/runtime/reload`
- `GET /api/v1/sessions/{session_id}/capability-requests`
- `POST /api/v1/sessions/{session_id}/capability-requests/{request_id}/dismiss`
- `POST /api/v1/sessions/{session_id}/capability-requests/{request_id}/resolve`
- `POST /api/v1/sessions/{session_id}/turns`
- `GET /api/v1/turns/{turn_id}`
- `GET /api/v1/turns/{turn_id}/stream`

## Admin Console

nano-claw now includes a Kubernetes-style read-only admin console:

- UI: `GET /admin`
- static assets: `GET /admin/static/*`
- API base: `/api/v1/admin/*`

Primary admin resources:

- `ServerOverview`
- `Session` / `SessionList`
- `SessionRuntime` / `SessionRuntimeList`
- `Turn` / `TurnList`
- `EventBusState`
- `AgentRuntime`
- `ToolRegistryState`
- `SkillCatalogState`
- `MCPServerState`
- `ExtensionBundle`
- `SubagentRun`
- `LogSession`
- `LogFile`
- `ConfigView`

Admin stream endpoint:

- `GET /api/v1/admin/stream?resources=<csv>&session_id=<optional>&interval_ms=<optional>`
- SSE event names: `snapshot`, `resource_changed`, `heartbeat`, `error`

Operational note:

- For long-running admin streams, monitor server file descriptors with `lsof -p <pid> | wc -l`; the count should stay stable rather than climb over time.

The admin UI is session-centric rather than flat-resource-centric:

- the left rail keeps global roots such as Overview, Sessions, Global Turns, Event Bus, and Config
- the main tree lets you expand a session into Context, Runtime, Agent, Skills, Tools, MCP, Subagents, Turns, and Logs
- the detail pane stays read-only with Summary, Related, and Raw JSON tabs

Read-only and safety guarantees:

- Admin endpoints are `GET` only.
- Prompt/output/log payloads are redacted and preview-truncated by default.
- Raw log bytes require explicit `log-files/download`.
- Log file access is constrained to the configured log root.

## Documentation

The front page stays focused on setup and concepts. Deeper implementation details live in `docs/`:

- [docs/design-overview.md](docs/design-overview.md) for architecture and the main ReAct loop
- [docs/subagents.md](docs/subagents.md) for delegated child-agent execution
- [docs/skills.md](docs/skills.md) for skill discovery and loading
- [docs/context-compaction.md](docs/context-compaction.md) for compaction strategy and lifecycle

## Development

Install dev dependencies and run tests:

```bash
pip install -r requirements-dev.txt
pytest
```

Useful directories:

```text
src/                core CLI, agent, config, logging, commands, and built-in tools
src/tools/          built-in tool implementations
src/commands/       slash command implementations
tests/              pytest suite
docs/               technical documentation
.githooks/          local secret guard hooks
.nano-claw/skills/ optional repo-local skills
```

## License

MIT
