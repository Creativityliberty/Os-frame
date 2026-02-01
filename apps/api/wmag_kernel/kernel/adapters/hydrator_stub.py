from __future__ import annotations
from typing import Any, Dict, List
from kernel.ports.hydrator import ContextHydrator

class StubHydrator(ContextHydrator):
    async def hydrate(self, tenant_id: str, user_message: str, node_list: List[str], registry: Dict[str, Any]) -> Dict[str, Any]:
        # Provide minimal context pack (enough for planner stub)
        return {
            "type":"context_pack",
            "pack_id":"pack_demo",
            "tenant_id": tenant_id,
            "task":{"user_message": user_message, "customer_ref":{"kind":"Customer","id":"cust_123"}},
            "node_list": node_list,
            "action_space":[{"action_id": a["action_id"], "tool": a["tool"]} for a in registry.get("actions", [])]
        }
