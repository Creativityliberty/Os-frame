from __future__ import annotations
from typing import Any, Dict, List

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:  # minimal fallback so imports don't break without pocketflow
        pass
from kernel.runtime.events import artifact_event
from kernel.nodes._helpers import emit

class LoadTenantContextNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"tenant_id": shared["task"]["tenant_id"], "task_id": shared["task"]["task_id"]}

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        return prep_res

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        storage = shared["deps"]["storage"]
        tenant = await storage.load_tenant_context(prep_res["tenant_id"])
        shared["tenant"] = tenant
        await emit(shared, artifact_event(prep_res["task_id"], shared["run"]["run_id"], "tenant", tenant))
        return "default"
