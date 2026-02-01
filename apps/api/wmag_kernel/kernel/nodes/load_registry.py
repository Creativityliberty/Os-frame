from __future__ import annotations
from typing import Any, Dict, List

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:  # minimal fallback so imports don't break without pocketflow
        pass
from kernel.runtime.events import artifact_event
from kernel.nodes._helpers import emit

class LoadRegistryNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"task": shared["task"], "task_id": shared["task"]["task_id"]}

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        return prep_res

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        reg_provider = shared["deps"]["registry"]
        reg = await reg_provider.load_registry_for(prep_res["task"]) if hasattr(reg_provider, "load_registry_for") else await reg_provider.load_registry(prep_res["task"]["tenant_id"])
        shared["registry"] = reg
        await emit(shared, artifact_event(prep_res["task_id"], shared["run"]["run_id"], "registry", {"registry_id": reg.get("registry_id"), "schema_version": reg.get("schema_version")}))
        return "default"
