from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import httpx

from kernel.ports.toolrunner import ToolRunner


@dataclass
class MCPServer:
    id: str
    base_url: str
    timeout_s: float = 15.0


class MCPHttpToolRunner(ToolRunner):
    """A minimal, real ToolRunner for MCP-like HTTP servers.

    Convention used (simple + practical):
      - list tools: GET {base_url}/tools
      - call tool:  POST {base_url}/call  with JSON {"tool": "name", "args": {...}}
    The tool string in registry should be:
      - "mcp:<server_id>/<tool_name>"
    Example:
      tool = "mcp:calendar/get_events"

    Env:
      MCP_SERVERS='[{"id":"calendar","base_url":"http://localhost:9100"}]'
    """

    def __init__(self, servers: List[MCPServer]) -> None:
        self.servers = {s.id: s for s in servers}
        self._client = httpx.AsyncClient()

    @classmethod
    def from_env(cls) -> "MCPHttpToolRunner":
        raw = os.getenv("MCP_SERVERS", "[]")
        arr = json.loads(raw)
        servers = [MCPServer(**x) for x in arr]
        return cls(servers)

    async def close(self) -> None:
        await self._client.aclose()

    def _parse_tool(self, tool: str) -> tuple[str, str]:
        if not tool.startswith("mcp:"):
            raise ValueError("Tool is not MCP: " + tool)
        rest = tool[len("mcp:"):]
        if "/" not in rest:
            raise ValueError("MCP tool must be mcp:<server>/<tool_name>")
        server_id, tool_name = rest.split("/", 1)
        return server_id, tool_name

    async def call(self, tenant_id: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        server_id, tool_name = self._parse_tool(tool)
        srv = self.servers.get(server_id)
        if not srv:
            raise ValueError(f"Unknown MCP server_id: {server_id}")

        url = srv.base_url.rstrip("/") + "/call"
        resp = await self._client.post(url, json={"tenant_id": tenant_id, "tool": tool_name, "args": args}, timeout=srv.timeout_s)
        resp.raise_for_status()
        data = resp.json()
        # expected: {"ok": true, "result": {...}} OR direct result
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data
