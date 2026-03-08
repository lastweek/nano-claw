---
name: web-research
description: Use this when the task needs public-web reading, webpage inspection, or link extraction, especially after getting candidate URLs from an MCP search tool.
metadata:
  short-description: Public web reading and MCP-assisted search workflow
---

Use the built-in web tools for public websites:

- `fetch_url`
- `read_webpage`
- `extract_page_links`

Preferred workflow:
1. If an MCP search tool is available, use it first to find relevant URLs.
2. Use `read_webpage` for human-readable page text and metadata.
3. Use `fetch_url` when the raw textual payload matters, such as JSON, XML, or HTML source.
4. Use `extract_page_links` to gather follow-up URLs from a page you already trust.

Limits and guardrails:
- These tools are read-only and only support public `http` and `https` targets.
- Localhost and private-network targets are blocked unless the runtime explicitly allows them.
- There is no built-in search tool in core v1. If no MCP search provider is configured, say search is unavailable and ask for a direct URL instead.
- Do not assume JavaScript rendering, browser tabs, cookies, login state, screenshots, or file downloads.
