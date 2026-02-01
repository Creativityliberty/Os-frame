from __future__ import annotations
from typing import Any, Dict, List

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:  # minimal fallback so imports don't break without pocketflow
        pass
from kernel.runtime.events import artifact_event
from kernel.nodes._helpers import emit

class LoadOrBuildContextTreesNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"tenant_id": shared["task"]["tenant_id"], "task_id": shared["task"]["task_id"]}

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        return prep_res

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        index = shared["deps"]["index"]
        trees = await index.load_or_build_trees(prep_res["tenant_id"], ["support", "customers"])
        shared["trees"] = trees
        await emit(shared, artifact_event(prep_res["task_id"], shared["run"]["run_id"], "context_trees", {"count": len(trees)}))
        return "default"
