from __future__ import annotations
from typing import Any, Dict, List
from kernel.ports.index import WorldIndexProvider

class InMemoryIndexProvider(WorldIndexProvider):
    async def load_or_build_trees(self, tenant_id: str, domains: List[str]) -> List[Dict[str, Any]]:
        # Minimal demo trees
        return [
            {"tree":"KB","nodes":[{"node_id":"SUPPORT/KB/Refunds","summary":"Refunds policy summary"}]},
            {"tree":"WORLD","nodes":[{"node_id":"CUSTOMERS/cust_123","summary":"Customer node"}]}
        ]
