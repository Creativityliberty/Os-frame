from __future__ import annotations
from typing import Any, Dict, List

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:  # minimal fallback so imports don't break without pocketflow
        pass
from kernel.runtime.events import artifact_event
from kernel.nodes._helpers import emit

class HydrateContextPackNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tenant_id": shared["task"]["tenant_id"],
            "task_id": shared["task"]["task_id"],
            "user_message": shared["task"]["user_message"],
            "node_list": (shared.get("node_selection") or {}).get("node_list", []),
            "registry": shared.get("registry") or {},
        }

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        return prep_res

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        hydrator = shared["deps"]["hydrator"]
        pack = await hydrator.hydrate(prep_res["tenant_id"], prep_res["user_message"], prep_res["node_list"], prep_res["registry"])
        shared["context_pack"] = pack
        await emit(shared, artifact_event(prep_res["task_id"], shared["run"]["run_id"], "context_pack", {"pack_id": pack.get("pack_id"), "nodes": len(prep_res["node_list"])}))
        return "default"
