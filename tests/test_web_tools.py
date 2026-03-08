"""Tests for public-web reading tools."""

import sys

import httpx

from src.config import Config
from src.context import Context
from src.skills import SkillManager
from src.tools import ToolProfile, build_tool_registry, build_tool_registry_with_report
from src.tools.web import (
    ExtractPageLinksTool,
    FetchURLTool,
    ReadWebpageTool,
    WebClient,
)


PUBLIC_IP = "93.184.216.34"


def make_web_client(handler, *, resolver=None, **kwargs) -> WebClient:
    """Create a deterministic WebClient backed by an httpx mock transport."""
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return WebClient(
        client=client,
        resolve_host=resolver or (lambda _hostname: [PUBLIC_IP]),
        **kwargs,
    )


def test_fetch_url_reads_plain_text_payload():
    """fetch_url should return decoded plain-text responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            text="hello from the internet",
            request=request,
        )

    tool = FetchURLTool(make_web_client(handler))
    result = tool.execute(Context.create(cwd="."), url="https://example.com/hello")

    assert result.success is True
    assert result.data["status_code"] == 200
    assert result.data["content_type"] == "text/plain; charset=utf-8"
    assert result.data["body_text"] == "hello from the internet"
    assert result.data["truncated"] is False


def test_fetch_url_reads_json_payload():
    """fetch_url should support JSON responses without special casing."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            text='{"ok":true,"items":[1,2]}',
            request=request,
        )

    tool = FetchURLTool(make_web_client(handler))
    result = tool.execute(Context.create(cwd="."), url="https://example.com/data.json")

    assert result.success is True
    assert result.data["content_type"] == "application/json"
    assert result.data["body_text"] == '{"ok":true,"items":[1,2]}'


def test_read_webpage_extracts_metadata_and_readable_text():
    """read_webpage should strip noisy HTML and expose basic metadata."""
    html = """
    <html>
      <head>
        <title>Ignored Title</title>
        <meta property="og:title" content="Primary Title" />
        <meta property="og:site_name" content="Example Site" />
        <meta property="article:published_time" content="2026-03-08T10:00:00Z" />
        <meta name="description" content="Short summary." />
      </head>
      <body>
        <nav>Navigation</nav>
        <main>
          <h1>Headline</h1>
          <p>First paragraph.</p>
          <p>Second paragraph.</p>
        </main>
        <footer>Footer text</footer>
      </body>
    </html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=html,
            request=request,
        )

    tool = ReadWebpageTool(make_web_client(handler))
    result = tool.execute(Context.create(cwd="."), url="https://example.com/post")

    assert result.success is True
    assert result.data["title"] == "Primary Title"
    assert result.data["site_name"] == "Example Site"
    assert result.data["published_at"] == "2026-03-08T10:00:00Z"
    assert result.data["excerpt"] == "Short summary."
    assert "Headline" in result.data["body_text"]
    assert "First paragraph." in result.data["body_text"]
    assert "Navigation" not in result.data["body_text"]
    assert "Footer text" not in result.data["body_text"]


def test_extract_page_links_normalizes_and_filters_links():
    """extract_page_links should normalize URLs, deduplicate, and respect same-domain filtering."""
    html = """
    <html><body>
      <a href="/docs" title="Docs">Docs</a>
      <a href="https://example.com/docs">Docs duplicate</a>
      <a href="https://other.example.org/page">Elsewhere</a>
      <a href="#fragment">Fragment</a>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=html,
            request=request,
        )

    tool = ExtractPageLinksTool(make_web_client(handler))
    result = tool.execute(
        Context.create(cwd="."),
        url="https://example.com/start",
        same_domain_only=True,
        limit=10,
    )

    assert result.success is True
    assert result.data["final_url"] == "https://example.com/start"
    assert result.data["links"] == [
        {
            "url": "https://example.com/docs",
            "text": "Docs",
            "title": "Docs",
            "same_domain": True,
        },
        {
            "url": "https://example.com/start",
            "text": "Fragment",
            "title": "",
            "same_domain": True,
        },
    ]


def test_fetch_url_blocks_localhost_and_private_networks():
    """Public-web tools should reject localhost targets by default."""
    tool = FetchURLTool(make_web_client(lambda request: httpx.Response(200, request=request)))

    result = tool.execute(Context.create(cwd="."), url="http://127.0.0.1:8765/")

    assert result.success is False
    assert "Blocked private or local network target" in result.error


def test_fetch_url_blocks_redirects_into_private_networks():
    """Redirect chains should be revalidated before following the next hop."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start":
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/private"},
                request=request,
            )
        raise AssertionError(f"Unexpected URL requested: {request.url}")

    tool = FetchURLTool(make_web_client(handler))
    result = tool.execute(Context.create(cwd="."), url="https://example.com/start")

    assert result.success is False
    assert "Blocked private or local network target" in result.error


def test_fetch_url_rejects_unsupported_content_types():
    """Binary content should fail clearly instead of returning unreadable bytes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n",
            request=request,
        )

    tool = FetchURLTool(make_web_client(handler))
    result = tool.execute(Context.create(cwd="."), url="https://example.com/logo.png")

    assert result.success is False
    assert result.error == "Unsupported content type: image/png"


def test_fetch_url_reports_timeouts_clearly():
    """Request timeouts should map to a stable error message."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    tool = FetchURLTool(make_web_client(handler, timeout_seconds=7))
    result = tool.execute(Context.create(cwd="."), url="https://example.com/slow")

    assert result.success is False
    assert result.error == "Web request timed out after 7 seconds"


def test_fetch_url_rejects_oversized_responses():
    """Responses larger than max_response_bytes should fail before decoding."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/plain",
                "content-length": "25",
            },
            text="x" * 25,
            request=request,
        )

    tool = FetchURLTool(make_web_client(handler, max_response_bytes=10))
    result = tool.execute(Context.create(cwd="."), url="https://example.com/large")

    assert result.success is False
    assert result.error == "Response exceeds max_response_bytes (10)"


def test_fetch_url_rejects_malformed_declared_charset():
    """Declared charset decode failures should surface a clear content error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            content=b"\xff\xfe\xff",
            request=request,
        )

    tool = FetchURLTool(make_web_client(handler))
    result = tool.execute(Context.create(cwd="."), url="https://example.com/bad-charset")

    assert result.success is False
    assert result.error == "Failed to decode response body with charset utf-8"


def test_fetch_url_can_allow_private_networks_when_enabled():
    """Private-network blocking should be configurable when explicitly enabled."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="local ok",
            request=request,
        )

    tool = FetchURLTool(
        make_web_client(
            handler,
            allow_private_networks=True,
            resolver=lambda _hostname: ["127.0.0.1"],
        )
    )
    result = tool.execute(Context.create(cwd="."), url="http://127.0.0.1/internal")

    assert result.success is True
    assert result.data["body_text"] == "local ok"


def test_build_tool_registry_registers_web_tools_for_build_subagent_and_plan_main(temp_dir, monkeypatch):
    """Web tools should be available in build, build subagent, and main planning profiles."""
    monkeypatch.delenv("WEB_TOOLS_ENABLED", raising=False)
    monkeypatch.delenv("WEB_TOOLS_ENABLE_FETCH_URL", raising=False)
    monkeypatch.delenv("WEB_TOOLS_ENABLE_READ_WEBPAGE", raising=False)
    monkeypatch.delenv("WEB_TOOLS_ENABLE_EXTRACT_PAGE_LINKS", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    runtime_config = Config({})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="linux",
    )
    skill_manager.discover()

    build_registry = build_tool_registry(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )
    build_subagent_registry = build_tool_registry(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD_SUBAGENT,
        runtime_config=runtime_config,
    )
    plan_main_registry = build_tool_registry(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.PLAN_MAIN,
        runtime_config=runtime_config,
    )
    plan_subagent_registry = build_tool_registry(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.PLAN_SUBAGENT,
        runtime_config=runtime_config,
    )

    expected = {"fetch_url", "read_webpage", "extract_page_links"}
    assert expected <= set(build_registry.list_tools())
    assert expected <= set(build_subagent_registry.list_tools())
    assert expected <= set(plan_main_registry.list_tools())
    assert expected.isdisjoint(set(plan_subagent_registry.list_tools()))


def test_build_tool_registry_report_marks_web_tools_disabled(temp_dir, monkeypatch):
    """Report output should explain when the web tool group is disabled."""
    monkeypatch.setattr(sys, "platform", "linux")
    runtime_config = Config({"web_tools": {"enabled": False}})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="linux",
    )
    skill_manager.discover()

    registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert "fetch_url" not in registry.list_tools()
    assert decisions["web_tools"] == "skipped: web_tools.enabled is false"
    assert decisions["fetch_url"] == "skipped: web_tools.enabled is false"
    assert decisions["read_webpage"] == "skipped: web_tools.enabled is false"
    assert decisions["extract_page_links"] == "skipped: web_tools.enabled is false"


def test_build_tool_registry_report_marks_only_disabled_web_tool_skipped(temp_dir, monkeypatch):
    """Per-tool disables should only remove the requested web tool."""
    monkeypatch.setattr(sys, "platform", "linux")
    runtime_config = Config({"web_tools": {"enable_extract_page_links": False}})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="linux",
    )
    skill_manager.discover()

    registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.BUILD,
        runtime_config=runtime_config,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert "fetch_url" in registry.list_tools()
    assert "read_webpage" in registry.list_tools()
    assert "extract_page_links" not in registry.list_tools()
    assert decisions["web_tools"] == "registered"
    assert decisions["fetch_url"] == "registered"
    assert decisions["read_webpage"] == "registered"
    assert decisions["extract_page_links"] == "skipped: web_tools.enable_extract_page_links is false"


def test_build_tool_registry_report_marks_profile_skip_for_web_tools(temp_dir, monkeypatch):
    """Plan subagents should not receive public-web tools."""
    monkeypatch.setattr(sys, "platform", "linux")
    runtime_config = Config({"web_tools": {"enabled": True}})
    skill_manager = SkillManager(
        repo_root=temp_dir,
        user_root=temp_dir / "user-skills",
        runtime_config=runtime_config,
        platform_name="linux",
    )
    skill_manager.discover()

    registry, report = build_tool_registry_with_report(
        skill_manager=skill_manager,
        tool_profile=ToolProfile.PLAN_SUBAGENT,
        runtime_config=runtime_config,
    )

    decisions = {
        decision.name: decision.status
        for decision in (*report.group_decisions, *report.tool_decisions)
    }

    assert "fetch_url" not in registry.list_tools()
    assert decisions["web_tools"] == (
        "skipped: tool profile is plan_subagent, requires build or build_subagent or plan_main"
    )
    assert decisions["fetch_url"] == (
        "skipped: tool profile is plan_subagent, requires build or build_subagent or plan_main"
    )
    assert decisions["read_webpage"] == (
        "skipped: tool profile is plan_subagent, requires build or build_subagent or plan_main"
    )
    assert decisions["extract_page_links"] == (
        "skipped: tool profile is plan_subagent, requires build or build_subagent or plan_main"
    )
