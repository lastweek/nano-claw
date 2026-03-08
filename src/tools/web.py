"""Public-web reading tools for fetching URLs, reading pages, and extracting links."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import ipaddress
import re
import socket
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx

from src.tools import Tool, ToolResult


USER_AGENT = "nano-claw/0.1"
MAX_REDIRECTS = 5
DEFAULT_FETCH_MAX_CHARS = 10_000
DEFAULT_READ_MAX_CHARS = 12_000
DEFAULT_LINK_LIMIT = 40
MAX_LINK_LIMIT = 200
HTML_MEDIA_TYPES = {"text/html", "application/xhtml+xml"}
TEXT_MEDIA_TYPES = {"application/json", "application/xml", "application/xhtml+xml"}
NOISY_HTML_SELECTORS = ("script", "style", "noscript", "nav", "footer", "aside", "form")


class WebToolError(Exception):
    """Base failure for public-web tools."""


class WebAccessError(WebToolError):
    """Blocked or invalid target error."""


class WebContentError(WebToolError):
    """Unsupported or malformed content error."""


@dataclass(frozen=True)
class FetchedResponse:
    """Normalized fetched text response."""

    url: str
    final_url: str
    status_code: int
    content_type: str
    media_type: str
    text: str
    truncated: bool


@dataclass
class WebClient:
    """HTTP helper with public-network guardrails and lightweight HTML parsing."""

    timeout_seconds: int = 15
    max_response_bytes: int = 2_000_000
    max_content_chars: int = 20_000
    allow_private_networks: bool = False
    client: httpx.Client | None = None
    resolve_host: Callable[[str], list[str]] | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            client_kwargs = {
                "follow_redirects": False,
                "timeout": self.timeout_seconds,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html, application/xhtml+xml, text/plain, application/json, application/xml;q=0.9, */*;q=0.1",
                },
            }
            try:
                self.client = httpx.Client(**client_kwargs)
            except AttributeError as exc:
                # Some local environments ship a broken certifi install; fall back to
                # the platform trust store so tool registration can still succeed.
                if "certifi" not in str(exc):
                    raise
                import ssl

                self.client = httpx.Client(
                    **client_kwargs,
                    verify=ssl.create_default_context(),
                )
        if self.resolve_host is None:
            self.resolve_host = self._default_resolve_host

    def fetch_text(self, url: str, *, max_chars: int) -> dict[str, Any]:
        fetched = self._fetch(
            url,
            max_chars=max_chars,
            allow_error_status=True,
            allow_html=True,
            truncate_result=True,
        )
        return {
            "url": fetched.url,
            "final_url": fetched.final_url,
            "status_code": fetched.status_code,
            "content_type": fetched.content_type,
            "body_text": fetched.text,
            "truncated": fetched.truncated,
        }

    def read_webpage(self, url: str, *, max_chars: int) -> dict[str, Any]:
        fetched = self._fetch(
            url,
            max_chars=max_chars,
            require_html=True,
            allow_error_status=False,
            truncate_result=False,
        )
        title, site_name, published_at, excerpt, body_text, truncated = self._parse_webpage(
            fetched.text,
            max_chars=max_chars,
        )
        return {
            "url": fetched.url,
            "final_url": fetched.final_url,
            "title": title,
            "site_name": site_name,
            "published_at": published_at,
            "excerpt": excerpt,
            "body_text": body_text,
            "truncated": truncated,
        }

    def extract_page_links(
        self,
        url: str,
        *,
        same_domain_only: bool,
        limit: int,
    ) -> dict[str, Any]:
        fetched = self._fetch(
            url,
            max_chars=self.max_content_chars,
            require_html=True,
            allow_error_status=False,
            truncate_result=False,
        )
        links = self._extract_links(
            fetched.text,
            base_url=fetched.final_url,
            same_domain_only=same_domain_only,
            limit=limit,
        )
        return {
            "url": fetched.url,
            "final_url": fetched.final_url,
            "links": links,
        }

    def _fetch(
        self,
        url: str,
        *,
        max_chars: int,
        allow_error_status: bool = False,
        allow_html: bool = False,
        require_html: bool = False,
        truncate_result: bool = True,
    ) -> FetchedResponse:
        current_url = self._normalize_url(url)

        for redirect_count in range(MAX_REDIRECTS + 1):
            self._validate_target(current_url)

            try:
                with self.client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebAccessError("Redirect response missing Location header")
                        if redirect_count == MAX_REDIRECTS:
                            raise WebAccessError(f"Too many redirects for URL: {url}")
                        current_url = self._normalize_url(urljoin(str(response.url), location))
                        continue

                    if not allow_error_status and response.status_code >= 400:
                        raise WebAccessError(f"Web request returned HTTP {response.status_code}")

                    content_type_header = response.headers.get("content-type", "").strip()
                    media_type, charset = _parse_content_type(content_type_header)
                    if not self._is_supported_media_type(
                        media_type,
                        allow_html=allow_html,
                        require_html=require_html,
                    ):
                        raise WebContentError(f"Unsupported content type: {media_type}")

                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_size = int(content_length)
                        except ValueError:
                            declared_size = None
                        if declared_size is not None and declared_size > self.max_response_bytes:
                            raise WebContentError(
                                f"Response exceeds max_response_bytes ({self.max_response_bytes})"
                            )

                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_response_bytes:
                            raise WebContentError(
                                f"Response exceeds max_response_bytes ({self.max_response_bytes})"
                            )

                    decoded = self._decode_body(bytes(body), charset=charset)
                    if truncate_result:
                        response_text, truncated = _truncate_text(decoded, max_chars)
                    else:
                        response_text = decoded.strip()
                        truncated = False
                    return FetchedResponse(
                        url=url,
                        final_url=str(response.url),
                        status_code=response.status_code,
                        content_type=content_type_header or media_type,
                        media_type=media_type,
                        text=response_text,
                        truncated=truncated,
                    )
            except httpx.TimeoutException as exc:
                raise WebToolError(
                    f"Web request timed out after {self.timeout_seconds} seconds"
                ) from exc
            except httpx.HTTPError as exc:
                raise WebToolError(f"Web request failed: {exc}") from exc

        raise WebAccessError(f"Too many redirects for URL: {url}")

    def _validate_target(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise WebAccessError("Only http and https URLs are supported")
        if not parsed.hostname:
            raise WebAccessError("URL must include a hostname")
        if parsed.username or parsed.password:
            raise WebAccessError("URLs with embedded credentials are not supported")
        if self.allow_private_networks:
            return
        self._ensure_public_hostname(parsed.hostname)

    def _ensure_public_hostname(self, hostname: str) -> None:
        normalized = hostname.strip().lower()
        if normalized == "localhost" or normalized.endswith(".localhost"):
            raise WebAccessError(f"Blocked private or local network target: {hostname}")

        try:
            ip_literal = ipaddress.ip_address(normalized)
        except ValueError:
            ip_literal = None

        if ip_literal is not None:
            if _is_blocked_ip(ip_literal):
                raise WebAccessError(f"Blocked private or local network target: {hostname}")
            return

        try:
            addresses = self.resolve_host(normalized)
        except OSError as exc:
            raise WebToolError(f"Unable to resolve host {hostname}: {exc}") from exc

        if not addresses:
            raise WebToolError(f"Unable to resolve host {hostname}")

        for address in addresses:
            try:
                parsed_ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise WebToolError(f"Host {hostname} resolved to an invalid address: {address}") from exc
            if _is_blocked_ip(parsed_ip):
                raise WebAccessError(f"Blocked private or local network target: {hostname}")

    def _default_resolve_host(self, hostname: str) -> list[str]:
        results = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        addresses = {entry[4][0] for entry in results if entry and entry[4]}
        return sorted(addresses)

    def _is_supported_media_type(
        self,
        media_type: str,
        *,
        allow_html: bool,
        require_html: bool,
    ) -> bool:
        if require_html:
            return media_type in HTML_MEDIA_TYPES
        if media_type.startswith("text/"):
            return True
        if media_type in TEXT_MEDIA_TYPES:
            return True
        if allow_html and media_type in HTML_MEDIA_TYPES:
            return True
        return False

    def _decode_body(self, body: bytes, *, charset: str | None) -> str:
        if not body:
            return ""
        if charset:
            try:
                return body.decode(charset)
            except LookupError as exc:
                raise WebContentError(f"Unsupported response charset: {charset}") from exc
            except UnicodeDecodeError as exc:
                raise WebContentError(f"Failed to decode response body with charset {charset}") from exc
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return body.decode("latin-1")

    def _parse_webpage(self, html_text: str, *, max_chars: int) -> tuple[str | None, str | None, str | None, str, str, bool]:
        soup = _parse_html(html_text)
        title = _first_nonempty(
            _meta_content(soup, property_name="og:title"),
            _document_title(soup),
        )
        site_name = _meta_content(soup, property_name="og:site_name")
        published_at = _first_nonempty(
            _meta_content(soup, property_name="article:published_time"),
            _meta_content(soup, name="article:published_time"),
            _meta_content(soup, name="date"),
            _meta_content(soup, name="publish-date"),
            _meta_content(soup, name="pubdate"),
            _time_datetime(soup),
        )
        excerpt = _first_nonempty(
            _meta_content(soup, name="description"),
            _meta_content(soup, property_name="og:description"),
        )
        body_text = _extract_readable_text(soup)
        body_text, truncated = _truncate_text(body_text, max_chars)
        if not excerpt:
            excerpt = body_text[:280].strip()
        return title, site_name, published_at, excerpt or "", body_text, truncated

    def _extract_links(
        self,
        html_text: str,
        *,
        base_url: str,
        same_domain_only: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        soup = _parse_html(html_text)
        base_host = (urlparse(base_url).hostname or "").lower()
        seen: set[str] = set()
        links: list[dict[str, Any]] = []

        for anchor in _iter_links(soup):
            href = str(anchor.get("href") or "").strip()
            if not href:
                continue
            normalized = urljoin(base_url, href)
            parsed = urlparse(normalized)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            clean_url = parsed._replace(fragment="").geturl()
            if clean_url in seen:
                continue
            same_domain = (parsed.hostname or "").lower() == base_host
            if same_domain_only and not same_domain:
                continue
            seen.add(clean_url)
            links.append(
                {
                    "url": clean_url,
                    "text": " ".join(anchor.get_text(" ", strip=True).split()),
                    "title": str(anchor.get("title") or "").strip(),
                    "same_domain": same_domain,
                }
            )
            if len(links) >= limit:
                break

        return links

    @staticmethod
    def _normalize_url(url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            raise WebAccessError("url is required")
        return raw


def _is_blocked_ip(address: ipaddress._BaseAddress) -> bool:
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
            getattr(address, "is_site_local", False),
        )
    )


def _parse_content_type(raw_value: str) -> tuple[str, str | None]:
    if not raw_value:
        return "application/octet-stream", None
    parts = [part.strip() for part in raw_value.split(";") if part.strip()]
    media_type = parts[0].lower()
    charset = None
    for part in parts[1:]:
        name, separator, value = part.partition("=")
        if separator and name.strip().lower() == "charset":
            charset = value.strip().strip('"').strip("'") or None
            break
    return media_type, charset


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return normalized, False
    return normalized[:max_chars].rstrip(), True


def _parse_html(html_text: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        return html_text
    return BeautifulSoup(html_text, "html.parser")


def _meta_content(soup, *, name: str | None = None, property_name: str | None = None) -> str | None:
    if isinstance(soup, str):
        attribute_name = "name" if name is not None else "property"
        attribute_value = name if name is not None else property_name
        if not attribute_value:
            return None
        pattern = re.compile(
            rf"<meta\b[^>]*\b{attribute_name}\s*=\s*['\"]{re.escape(attribute_value)}['\"][^>]*\bcontent\s*=\s*['\"]([^'\"]+)['\"][^>]*>",
            re.IGNORECASE,
        )
        match = pattern.search(soup)
        if match is None:
            return None
        value = unescape(match.group(1)).strip()
        return value or None
    attrs = {}
    if name is not None:
        attrs["name"] = name
    if property_name is not None:
        attrs["property"] = property_name
    tag = soup.find("meta", attrs=attrs)
    if tag is None:
        return None
    content = tag.get("content")
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    return stripped or None


def _time_datetime(soup) -> str | None:
    if isinstance(soup, str):
        match = re.search(
            r"<time\b[^>]*\bdatetime\s*=\s*['\"]([^'\"]+)['\"][^>]*>",
            soup,
            re.IGNORECASE,
        )
        if match is None:
            return None
        value = unescape(match.group(1)).strip()
        return value or None
    tag = soup.find("time", attrs={"datetime": True})
    if tag is None:
        return None
    value = tag.get("datetime")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _first_nonempty(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _document_title(soup) -> str | None:
    if isinstance(soup, str):
        match = re.search(r"<title\b[^>]*>(.*?)</title>", soup, re.IGNORECASE | re.DOTALL)
        if match is None:
            return None
        title = _strip_html(match.group(1)).strip()
        return title or None
    return soup.title.string.strip() if soup.title and soup.title.string else None


def _extract_readable_text(soup) -> str:
    if isinstance(soup, str):
        html_text = soup
        for selector in ("script", "style", "noscript", "nav", "footer", "aside", "form"):
            html_text = re.sub(
                rf"<{selector}\b[^>]*>.*?</{selector}>",
                " ",
                html_text,
                flags=re.IGNORECASE | re.DOTALL,
            )
        root_match = re.search(
            r"<(main|article)\b[^>]*>(.*?)</\1>",
            html_text,
            re.IGNORECASE | re.DOTALL,
        )
        if root_match is None:
            root_match = re.search(r"<body\b[^>]*>(.*?)</body>", html_text, re.IGNORECASE | re.DOTALL)
        root_html = root_match.group(2 if root_match.lastindex and root_match.lastindex > 1 else 1) if root_match else html_text
        text = _strip_html(root_html)
        lines = [" ".join(line.split()) for line in text.splitlines()]
        filtered = [line for line in lines if line]
        return "\n".join(filtered)
    for selector in NOISY_HTML_SELECTORS:
        for node in soup.select(selector):
            node.decompose()

    root = soup.select_one("main, article, [role='main']") or soup.body or soup
    text = root.get_text("\n", strip=True)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    filtered = [line for line in lines if line]
    return "\n".join(filtered)


def _strip_html(html_text: str) -> str:
    block_tags = ("p", "div", "section", "article", "main", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "br")
    normalized = html_text
    for tag_name in block_tags:
        normalized = re.sub(rf"</?{tag_name}\b[^>]*>", "\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    return unescape(normalized)


def _iter_links(soup):
    if not isinstance(soup, str):
        return soup.find_all("a", href=True)

    class _FallbackAnchor:
        def __init__(self, attrs: dict[str, str], inner_html: str) -> None:
            self._attrs = attrs
            self._inner_html = inner_html

        def get(self, name: str):
            return self._attrs.get(name)

        def get_text(self, _separator: str = " ", strip: bool = False):
            text = _strip_html(self._inner_html)
            return text.strip() if strip else text

    anchors = []
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", soup, re.IGNORECASE | re.DOTALL):
        attrs_text, inner_html = match.groups()
        attrs = {
            attr_name.lower(): unescape(attr_value)
            for attr_name, attr_value in re.findall(
                r"([A-Za-z_:][A-Za-z0-9_:\-]*)\s*=\s*['\"]([^'\"]*)['\"]",
                attrs_text,
            )
        }
        if "href" not in attrs:
            continue
        anchors.append(_FallbackAnchor(attrs, inner_html))
    return anchors


def _require_int(
    kwargs: dict[str, Any],
    name: str,
    *,
    default: int,
    minimum: int = 1,
    maximum: int = 100_000,
) -> int:
    value = kwargs.get(name, default)
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _require_bool(kwargs: dict[str, Any], name: str, *, default: bool = False) -> bool:
    value = kwargs.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _require_string(kwargs: dict[str, Any], name: str) -> str:
    value = kwargs.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


class FetchURLTool(Tool):
    """Read-only raw URL fetch tool."""

    name = "fetch_url"
    description = (
        "Fetch a public http/https URL and return its decoded text payload. "
        "Supports text, HTML, JSON, and XML responses."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public http/https URL to fetch."},
            "max_chars": {
                "type": "integer",
                "description": "Maximum body_text characters to return.",
                "default": DEFAULT_FETCH_MAX_CHARS,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, client: WebClient) -> None:
        self.client = client

    def execute(self, context, **kwargs) -> ToolResult:
        del context
        try:
            url = _require_string(kwargs, "url")
            max_chars = _require_int(
                kwargs,
                "max_chars",
                default=DEFAULT_FETCH_MAX_CHARS,
                maximum=self.client.max_content_chars,
            )
            return ToolResult(success=True, data=self.client.fetch_text(url, max_chars=max_chars))
        except (ValueError, WebToolError) as exc:
            return ToolResult(success=False, error=str(exc))


class ReadWebpageTool(Tool):
    """Readable webpage extraction tool."""

    name = "read_webpage"
    description = (
        "Read a public webpage and extract readable article/page text plus basic metadata."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public http/https webpage URL to read."},
            "max_chars": {
                "type": "integer",
                "description": "Maximum extracted body_text characters to return.",
                "default": DEFAULT_READ_MAX_CHARS,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, client: WebClient) -> None:
        self.client = client

    def execute(self, context, **kwargs) -> ToolResult:
        del context
        try:
            url = _require_string(kwargs, "url")
            max_chars = _require_int(
                kwargs,
                "max_chars",
                default=DEFAULT_READ_MAX_CHARS,
                maximum=self.client.max_content_chars,
            )
            return ToolResult(success=True, data=self.client.read_webpage(url, max_chars=max_chars))
        except (ValueError, WebToolError) as exc:
            return ToolResult(success=False, error=str(exc))


class ExtractPageLinksTool(Tool):
    """HTML link extraction tool."""

    name = "extract_page_links"
    description = (
        "Extract normalized http/https links from a public webpage."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public http/https webpage URL to inspect."},
            "same_domain_only": {
                "type": "boolean",
                "description": "Keep only links on the same hostname as the fetched page.",
                "default": False,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of links to return.",
                "default": DEFAULT_LINK_LIMIT,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, client: WebClient) -> None:
        self.client = client

    def execute(self, context, **kwargs) -> ToolResult:
        del context
        try:
            url = _require_string(kwargs, "url")
            same_domain_only = _require_bool(kwargs, "same_domain_only", default=False)
            limit = _require_int(kwargs, "limit", default=DEFAULT_LINK_LIMIT, maximum=MAX_LINK_LIMIT)
            return ToolResult(
                success=True,
                data=self.client.extract_page_links(
                    url,
                    same_domain_only=same_domain_only,
                    limit=limit,
                ),
            )
        except (ValueError, WebToolError) as exc:
            return ToolResult(success=False, error=str(exc))
