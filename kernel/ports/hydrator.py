from __future__ import annotations
from typing import Any, Dict, List, Protocol

class ContextHydrator(Protocol):
    async def hydrate(self, tenant_id: str, user_message: str, node_list: List[str], registry: Dict[str, Any]) -> Dict[str, Any]: ...
