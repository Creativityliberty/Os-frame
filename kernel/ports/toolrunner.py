from __future__ import annotations
from typing import Any, Dict, Protocol

class ToolRunner(Protocol):
    async def call(self, tenant_id: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]: ...
