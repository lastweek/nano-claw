"""MCP (Model Context Protocol) runtime support."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

import httpx

from src.tools import Tool, ToolRegistry, ToolResult


JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
LEGACY_PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOL_VERSIONS = (DEFAULT_PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION)
SESSION_ID_HEADER = "Mcp-Session-Id"
PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
ACCEPT_HEADER = "application/json, text/event-stream"
USER_AGENT = "nano-claw/0.1"


@dataclass
class MCPServer:
    """Manages connection to a single MCP server."""

    name: str
    url: str
    timeout: int = 30
    client: Optional[httpx.Client] = None
    debug: bool = False
    protocol_version: str = field(default=DEFAULT_PROTOCOL_VERSION, init=False)
    session_id: Optional[str] = field(default=None, init=False)
    server_capabilities: Dict[str, Any] = field(default_factory=dict, init=False)
    server_info: Dict[str, Any] = field(default_factory=dict, init=False)
    _initialized: bool = field(default=False, init=False)
    _request_id: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        """Initialize the HTTP client after dataclass construction."""
        self.url = self.url.rstrip("/")

        if self.debug:
            print(f"[MCP:{self.name}] Creating HTTP client (timeout={self.timeout}s)")

        if self.client is None:
            self.client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"Accept": ACCEPT_HEADER, "User-Agent": USER_AGENT},
            )

        if self.debug:
            print(f"[MCP:{self.name}] HTTP client created")

    def _next_request_id(self) -> int:
        """Generate the next JSON-RPC request id."""
        self._request_id += 1
        return self._request_id

    def _build_headers(
        self,
        *,
        include_session: bool,
        protocol_version: Optional[str] = None,
    ) -> Dict[str, str]:
        """Build HTTP headers for MCP requests."""
        headers = {
            "Accept": ACCEPT_HEADER,
            "Content-Type": "application/json",
        }

        if include_session and self.session_id:
            headers[SESSION_ID_HEADER] = self.session_id

        if protocol_version:
            headers[PROTOCOL_VERSION_HEADER] = protocol_version

        return headers

    def _store_session_id(self, response: httpx.Response) -> None:
        """Capture the MCP session id from a response when present."""
        session_id = response.headers.get(SESSION_ID_HEADER)
        if session_id:
            self.session_id = session_id

    def _parse_sse_response(self, body: str, request_id: int) -> Dict[str, Any]:
        """Extract the matching JSON-RPC message from an SSE response body."""
        data_lines: List[str] = []

        def parse_event(lines: List[str]) -> Optional[Dict[str, Any]]:
            if not lines:
                return None

            payload_text = "\n".join(lines).strip()
            if not payload_text or payload_text == "[DONE]":
                return None

            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError as exc:
                raise ConnectionError(
                    f"MCP server '{self.name}' returned invalid SSE JSON: {exc}"
                ) from exc

            return self._extract_response_message(payload, request_id)

        for line in body.splitlines():
            if not line:
                message = parse_event(data_lines)
                if message is not None:
                    return message
                data_lines = []
                continue

            if line.startswith(":"):
                continue

            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        message = parse_event(data_lines)
        if message is not None:
            return message

        raise ConnectionError(
            f"MCP server '{self.name}' did not return a JSON-RPC response for request {request_id}"
        )

    @staticmethod
    def _iter_messages(payload: Any) -> Iterable[Dict[str, Any]]:
        """Iterate over JSON-RPC messages from an object or batch."""
        if isinstance(payload, dict):
            yield payload
            return

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    yield item

    def _extract_response_message(
        self,
        payload: Any,
        request_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Find the JSON-RPC response matching a request id."""
        for message in self._iter_messages(payload):
            if message.get("id") == request_id:
                return message
        return None

    def _decode_response(self, response: httpx.Response, request_id: int) -> Dict[str, Any]:
        """Decode a JSON or SSE MCP response."""
        if not response.content:
            raise ConnectionError(f"MCP server '{self.name}' returned an empty response")

        content_type = response.headers.get("content-type", "").lower()

        if "text/event-stream" in content_type:
            message = self._parse_sse_response(response.text, request_id)
        else:
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise ConnectionError(
                    f"MCP server '{self.name}' returned invalid JSON: {exc}"
                ) from exc

            message = self._extract_response_message(payload, request_id)

        if message is None:
            raise ConnectionError(
                f"MCP server '{self.name}' did not return a JSON-RPC response for request {request_id}"
            )

        return message

    def _post_jsonrpc(
        self,
        method: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        include_session: bool = True,
        notification: bool = False,
        protocol_version: Optional[str] = None,
        retry_on_session_expiry: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Send one JSON-RPC message to the MCP server."""
        import time

        body: Dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
        request_id: Optional[int] = None

        if not notification:
            request_id = self._next_request_id()
            body["id"] = request_id

        if params is not None:
            body["params"] = params

        headers = self._build_headers(
            include_session=include_session,
            protocol_version=protocol_version,
        )

        if self.debug:
            print(f"[MCP:{self.name}] POST {self.url} ({method})")
            start_time = time.time()

        try:
            response = self.client.post(
                self.url,
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
            self._store_session_id(response)

            if (
                response.status_code == 404
                and retry_on_session_expiry
                and include_session
                and self.session_id
            ):
                if self.debug:
                    print(f"[MCP:{self.name}] Session expired, reinitializing")
                self._reset_session()
                self._initialize()
                return self._post_jsonrpc(
                    method,
                    params=params,
                    include_session=include_session,
                    notification=notification,
                    protocol_version=self.protocol_version,
                    retry_on_session_expiry=False,
                )

            response.raise_for_status()

            if self.debug:
                elapsed = time.time() - start_time
                print(f"[MCP:{self.name}] Response {response.status_code} ({elapsed:.2f}s)")

            if notification:
                return None

            return self._decode_response(response, request_id)
        except httpx.TimeoutException as exc:
            if self.debug:
                elapsed = time.time() - start_time
                print(f"[MCP:{self.name}] TIMEOUT after {elapsed:.2f}s")
            raise TimeoutError(
                f"MCP server '{self.name}' timed out after {self.timeout}s"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if self.debug:
                elapsed = time.time() - start_time
                print(f"[MCP:{self.name}] ERROR {exc.response.status_code} ({elapsed:.2f}s)")
            raise ConnectionError(
                f"MCP server '{self.name}' returned HTTP "
                f"{exc.response.status_code}: {exc.response.text}"
            ) from exc

    def _reset_session(self) -> None:
        """Forget any negotiated MCP session state."""
        self.session_id = None
        self.server_capabilities = {}
        self.server_info = {}
        self.protocol_version = DEFAULT_PROTOCOL_VERSION
        self._initialized = False

    def _initialize(self) -> None:
        """Initialize the MCP session and negotiate protocol details."""
        last_error: Optional[Exception] = None

        for candidate_version in SUPPORTED_PROTOCOL_VERSIONS:
            self._reset_session()

            try:
                response = self._post_jsonrpc(
                    "initialize",
                    params={
                        "protocolVersion": candidate_version,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "nano-claw",
                            "version": "0.1.0",
                        },
                    },
                    include_session=False,
                    protocol_version=None,
                )

                if response is None:
                    raise ConnectionError(
                        f"MCP server '{self.name}' did not respond to initialize"
                    )

                if "error" in response:
                    error = response["error"]
                    raise ConnectionError(
                        f"MCP server '{self.name}' returned error: "
                        f"{error.get('message', 'Unknown error')}"
                    )

                result = response.get("result", {})
                negotiated_version = result.get("protocolVersion", candidate_version)

                if negotiated_version not in SUPPORTED_PROTOCOL_VERSIONS:
                    raise ConnectionError(
                        f"MCP server '{self.name}' negotiated unsupported protocol "
                        f"version '{negotiated_version}'"
                    )

                self.protocol_version = negotiated_version
                self.server_capabilities = result.get("capabilities", {})
                self.server_info = result.get("serverInfo", {})

                self._post_jsonrpc(
                    "notifications/initialized",
                    params={},
                    notification=True,
                    protocol_version=self.protocol_version,
                )
                self._initialized = True
                return
            except ConnectionError as exc:
                last_error = exc
                message = str(exc).lower()
                if "protocol" not in message and "version" not in message:
                    break

        if last_error is not None:
            raise last_error

        raise ConnectionError(f"MCP server '{self.name}' failed to initialize")

    def _ensure_initialized(self) -> None:
        """Initialize the server session on first use."""
        if not self._initialized:
            self._initialize()

    @staticmethod
    def _extract_text_content(content: Any) -> str:
        """Extract plain text from MCP content blocks when possible."""
        if not isinstance(content, list):
            return ""

        text_blocks = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n\n".join(part for part in text_blocks if part)

    def _normalize_tool_result(self, result: Any) -> Any:
        """Convert a standard MCP tool result into agent-friendly data."""
        if not isinstance(result, dict):
            return result

        text_output = self._extract_text_content(result.get("content"))
        structured_output = result.get("structuredContent")

        if text_output and structured_output is None:
            return text_output

        if structured_output is not None and not text_output:
            return structured_output

        if text_output and structured_output is not None:
            return {
                "text": text_output,
                "structuredContent": structured_output,
            }

        return result

    def list_tools(self) -> List[Dict[str, Any]]:
        """Get available tools from the MCP server."""
        self._ensure_initialized()

        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            params = {"cursor": cursor} if cursor else None
            response = self._post_jsonrpc(
                "tools/list",
                params=params,
                protocol_version=self.protocol_version,
            )

            if response is None:
                raise ConnectionError(
                    f"MCP server '{self.name}' returned an empty tools/list response"
                )

            if "error" in response:
                error = response["error"]
                raise ConnectionError(
                    f"MCP server '{self.name}' returned error: "
                    f"{error.get('message', 'Unknown error')}"
                )

            result = response.get("result", {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")

            if not cursor:
                return tools

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool on the MCP server."""
        try:
            self._ensure_initialized()
            response = self._post_jsonrpc(
                "tools/call",
                params={"name": tool_name, "arguments": arguments},
                protocol_version=self.protocol_version,
            )

            if response is None:
                return {"error": "MCP server returned an empty tools/call response"}

            if "error" in response:
                error = response["error"]
                return {"error": error.get("message", "Unknown error")}

            result = response.get("result", {})

            if isinstance(result, dict) and result.get("isError"):
                text_output = self._extract_text_content(result.get("content"))
                return {"error": text_output or str(result)}

            return {"data": self._normalize_tool_result(result)}
        except TimeoutError:
            return {"error": f"Tool execution timed out after {self.timeout}s"}
        except ConnectionError as exc:
            return {"error": str(exc)}

    def health_check(self) -> bool:
        """Check if the MCP server is healthy by performing initialization."""
        try:
            self._ensure_initialized()
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Close the HTTP client and best-effort close the MCP session."""
        try:
            if self.session_id:
                self.client.delete(
                    self.url,
                    headers=self._build_headers(
                        include_session=True,
                        protocol_version=self.protocol_version if self._initialized else None,
                    ),
                    timeout=min(self.timeout, 5),
                )
        except Exception:
            pass
        finally:
            self._reset_session()
            self.client.close()

    def __enter__(self) -> "MCPServer":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class MCPTool(Tool):
    """Expose MCP server tools as native Tool objects."""

    def __init__(self, server: MCPServer, tool_def: dict):
        """Initialize the MCP tool adapter."""
        self._server = server
        self._tool_def = tool_def

    @property
    def name(self) -> str:
        """Return the namespaced tool name."""
        return f"{self._server.name}:{self._tool_def['name']}"

    @property
    def description(self) -> str:
        """Return the human-readable tool description."""
        base_desc = self._tool_def.get("description", "")
        if not base_desc:
            return f"Tool from {self._server.name}"
        return f"{base_desc} (via {self._server.name})"

    @property
    def parameters(self) -> dict:
        """Return the MCP tool parameter schema."""
        input_schema = self._tool_def.get("inputSchema")
        if isinstance(input_schema, dict):
            schema = deepcopy(input_schema)
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})
            return schema

        properties = {}
        required = []

        for param in self._tool_def.get("parameters", []):
            param_name = param.get("name", "")
            if not param_name:
                continue

            schema = {
                "type": param.get("type", "string"),
                "description": param.get("description", ""),
            }

            if "default" in param:
                schema["default"] = param["default"]

            if "enum" in param:
                schema["enum"] = param["enum"]

            properties[param_name] = schema

            if param.get("required", False):
                required.append(param_name)

        result = {"type": "object", "properties": properties}
        if required:
            result["required"] = required
        return result

    def execute(self, context: Any, **kwargs) -> ToolResult:
        """Execute the MCP-backed tool."""
        del context
        tool_name = self._tool_def["name"]

        try:
            result = self._server.call_tool(tool_name, kwargs)

            if result.get("error"):
                return ToolResult(success=False, error=result["error"])

            return ToolResult(success=True, data=result.get("data"))
        except TimeoutError as exc:
            return ToolResult(success=False, error=str(exc))
        except ConnectionError as exc:
            return ToolResult(success=False, error=f"MCP server connection failed: {exc}")
        except Exception as exc:
            return ToolResult(success=False, error=f"Unexpected MCP error: {exc}")

    def to_schema(self) -> dict:
        """Convert the tool to OpenAI function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class MCPManager:
    """Manage multiple MCP servers and their tools."""

    def __init__(self, servers_config: List[Dict[str, Any]], debug: bool = False):
        """Initialize the MCP manager from configuration."""
        self._debug = debug
        self._servers: Dict[str, MCPServer] = {}
        self._cached_tool_defs: Dict[str, List[Dict[str, Any]]] = {}

        if self._debug:
            print(f"[MCP] Initializing MCP manager with {len(servers_config)} server(s)")

        self._load_servers(servers_config)

    def _load_servers(self, configs: List[Dict[str, Any]]) -> None:
        """Initialize MCP servers from configuration dictionaries."""
        for server_config in configs:
            if server_config.get("enabled", True):
                name = server_config["name"]
                url = server_config["url"]

                if self._debug:
                    print(f"[MCP] Loading server: {name} ({url})")

                server = MCPServer(
                    name=name,
                    url=url,
                    timeout=server_config.get("timeout", 30),
                    debug=self._debug,
                )
                self._servers[name] = server

                if self._debug:
                    print(f"[MCP] Server {name} loaded successfully")

    def register_tools(self, registry: ToolRegistry) -> None:
        """Discover and register tools from all MCP servers."""
        if self._debug:
            print(f"[MCP] Starting tool discovery for {len(self._servers)} server(s)")

        for server in self._servers.values():
            try:
                if server.name in self._cached_tool_defs:
                    tools_defs = deepcopy(self._cached_tool_defs[server.name])
                    if self._debug:
                        print(f"[MCP] Reusing cached tools for {server.name}")
                else:
                    if self._debug:
                        print(f"[MCP] Contacting {server.name} for tool list...")

                    tools_defs = server.list_tools()
                    self._cached_tool_defs[server.name] = deepcopy(tools_defs)

                if self._debug:
                    print(f"[MCP] Found {len(tools_defs)} tool(s) from {server.name}")

                for tool_def in tools_defs:
                    registry.register(MCPTool(server, tool_def))
            except Exception as exc:
                if self._debug:
                    print(f"[MCP] ERROR: Failed to load tools from {server.name}: {exc}")
                else:
                    print(f"\033[33mWarning: Failed to load tools from {server.name}: {exc}\033[0m")

        if self._debug:
            print("[MCP] Tool discovery complete")

    def clear_tool_cache(self) -> None:
        """Clear cached MCP tool definitions so discovery runs again."""
        self._cached_tool_defs.clear()

    def get_server_status(self) -> Dict[str, bool]:
        """Check the health status of all MCP servers."""
        status = {}
        for name, server in self._servers.items():
            status[name] = server.health_check()
        return status

    def close_all(self) -> None:
        """Close all MCP server connections."""
        for server in self._servers.values():
            server.close()


__all__ = [
    "ACCEPT_HEADER",
    "DEFAULT_PROTOCOL_VERSION",
    "JSONRPC_VERSION",
    "LEGACY_PROTOCOL_VERSION",
    "MCPManager",
    "MCPServer",
    "MCPTool",
    "PROTOCOL_VERSION_HEADER",
    "SESSION_ID_HEADER",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "USER_AGENT",
]
