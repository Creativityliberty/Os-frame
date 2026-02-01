from __future__ import annotations
from typing import Any, Dict, List, Protocol

class WorldIndexProvider(Protocol):
    async def load_or_build_trees(self, tenant_id: str, domains: List[str]) -> List[Dict[str, Any]]: ...
