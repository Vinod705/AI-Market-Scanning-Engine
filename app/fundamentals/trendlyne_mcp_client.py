"""Minimal MCP-over-HTTP client for the Trendlyne MCP server.

Trendlyne's MCP server (`Trendlyne-Financial-Server`, discovered live during
Phase 7) is a stateless, HTTP-based MCP server: no session handshake is
required — a bare `tools/call` JSON-RPC POST against the account's remote
MCP URL (auth is carried entirely in the URL's `token` query parameter,
"No Auth" at the protocol level per Trendlyne's own docs) is sufficient.
This is not a generic MCP client — it implements only what's needed to call
Trendlyne's tools, over the transport it was actually observed to use.

Two things discovered by live testing that are NOT optional:
1. The server responds with SSE framing (`Content-Type: text/event-stream`,
   body shaped `event: message\ndata: {...}\n\n`) even for a single
   request/response pair — this client parses that instead of assuming a
   bare JSON body.
2. nginx in front of the server returns a bare 403 for requests without a
   browser-like `User-Agent` header. This is not a bypass of any access
   control — the token is what authorizes the request — it is simply what
   a real client of this authorized endpoint has to send.

The account's MCP URL (`Settings.trendlyne_mcp_url`) is bearer-equivalent
and is never logged, never included in an exception message, and never
persisted anywhere by this module.
"""

import json
from typing import Any

import httpx
from loguru import logger

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_ACCEPT = "application/json, text/event-stream"


class TrendlyneMcpError(Exception):
    """Raised for any Trendlyne MCP failure. Never carries the request URL
    (which contains the token) in its message."""


def _parse_sse_json(body: str) -> dict[str, Any]:
    """Extracts the last `data:` payload from an SSE-framed response body
    and parses it as JSON. Raises TrendlyneMcpError if none is found or it
    isn't valid JSON-RPC."""
    data_lines = [
        line[len("data:") :].strip() for line in body.splitlines() if line.startswith("data:")
    ]
    if not data_lines:
        raise TrendlyneMcpError("Trendlyne MCP response had no data: line")
    try:
        payload: dict[str, Any] = json.loads(data_lines[-1])
    except ValueError as exc:
        raise TrendlyneMcpError("Trendlyne MCP response was not valid JSON") from exc
    return payload


class TrendlyneMcpClient:
    def __init__(
        self, mcp_url: str, timeout_seconds: float, client: httpx.AsyncClient | None = None
    ) -> None:
        self._mcp_url = mcp_url
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": _USER_AGENT},
        )
        self._request_id = 0

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Calls one Trendlyne MCP tool and returns its raw text content.
        Each tool has its own text/format conventions (plain key-value
        tables, JSON-in-a-string, ...) — parsing that is the caller's job,
        this method only speaks MCP transport.

        Raises TrendlyneMcpError on any transport, protocol, or tool-level
        error — callers (see `TrendlyneFundamentalDataProvider`) must catch
        this and degrade to UNKNOWN, never let it propagate into the
        scanner pipeline."""
        self._request_id += 1
        request_body = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        try:
            response = await self._client.post(
                self._mcp_url,
                json=request_body,
                headers={"Content-Type": "application/json", "Accept": _ACCEPT},
            )
        except httpx.TimeoutException as exc:
            raise TrendlyneMcpError(f"request to tool {name!r} timed out") from exc
        except httpx.HTTPError as exc:
            raise TrendlyneMcpError(
                f"request to tool {name!r} failed: {exc.__class__.__name__}"
            ) from exc

        if response.status_code != 200:
            raise TrendlyneMcpError(f"tool {name!r} returned HTTP {response.status_code}")

        payload = _parse_sse_json(response.text)

        if "error" in payload:
            error = payload["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise TrendlyneMcpError(f"tool {name!r} returned an MCP error: {message}")

        result = payload.get("result")
        if not isinstance(result, dict):
            raise TrendlyneMcpError(f"tool {name!r} returned no result")
        if result.get("isError"):
            raise TrendlyneMcpError(f"tool {name!r} reported isError")

        content = result.get("content")
        if not isinstance(content, list) or not content:
            raise TrendlyneMcpError(f"tool {name!r} returned no content")
        text = content[0].get("text") if isinstance(content[0], dict) else None
        if not isinstance(text, str):
            raise TrendlyneMcpError(f"tool {name!r} returned non-text content")
        return text

    async def health_check(self) -> bool:
        """A cheap `tools/list` call — used only to report /health status,
        never logs the URL/token."""
        self._request_id += 1
        request_body = {"jsonrpc": "2.0", "id": self._request_id, "method": "tools/list"}
        try:
            response = await self._client.post(
                self._mcp_url,
                json=request_body,
                headers={"Content-Type": "application/json", "Accept": _ACCEPT},
            )
            if response.status_code != 200:
                return False
            payload = _parse_sse_json(response.text)
            return "result" in payload and "tools" in payload["result"]
        except (httpx.HTTPError, TrendlyneMcpError) as exc:
            logger.warning(
                "Trendlyne MCP health check failed: {error}", error=exc.__class__.__name__
            )
            return False
