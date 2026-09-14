"""AgentCore Gateway MCP access: client construction, direct tool calls, and
fail-closed denial classification (C1).

The Gateway URL and the caller's bearer JWT arrive per request (C4), so a fresh
:class:`MCPClient` is built for each invocation -- never shared across sessions or
users.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Protocol

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

_DENIED_RE = re.compile(r"DENIED:(?P<layer>[a-zA-Z_]+):(?P<reason>.*)", re.DOTALL)
_POLICY_DENIAL_MARKERS = ("-32002", "Tool Execution Denied")


def build_gateway_client(gateway_url: str, token: str) -> MCPClient:
    """Build an MCP client for the Gateway, authenticated with the caller's own bearer JWT."""
    return MCPClient(lambda: streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {token}"}))


def result_text(result: Any) -> str:
    """Concatenate the text content blocks of a tool result (MCP or ``@tool``-decorated)."""
    if not isinstance(result, dict):
        return ""
    parts = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "\n".join(parts)


def classify_denial(text: str) -> tuple[str, str] | None:
    """Classify a tool-result error text as a fail-closed denial, or return ``None``.

    A Lambda-layer check reports ``DENIED:<layer>:<reason>`` verbatim (C1); a Gateway
    policy denial instead carries Bedrock AgentCore's own ``-32002``/"Tool Execution
    Denied" text. Returns ``(layer, reason)``.
    """
    if not text:
        return None
    match = _DENIED_RE.search(text)
    if match:
        return match.group("layer"), match.group("reason").strip()
    if any(marker in text for marker in _POLICY_DENIAL_MARKERS):
        return "policy", text.strip()
    return None


class GatewayCaller(Protocol):
    """Anything that can invoke a Gateway tool directly by name (real MCPClient or a fake)."""

    def call_tool_sync(self, tool_use_id: str, name: str, arguments: dict[str, Any] | None = None) -> Any: ...


def call_gateway_tool(caller: GatewayCaller, name: str, **arguments: Any) -> Any:
    """Call one Gateway tool directly (outside of a model turn) and return its parsed output.

    Used by subagents that need to write case state themselves (``case___save_extraction``,
    ``case___draft_action``) rather than asking the top-level model to do it.

    Raises:
        RuntimeError: if the tool call failed or was denied at any layer.
    """
    result = caller.call_tool_sync(str(uuid.uuid4()), name, arguments)
    text = result_text(result)
    status = result.get("status") if isinstance(result, dict) else "success"
    if status != "success":
        raise RuntimeError(text or f"gateway tool call failed: {name}")
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text
