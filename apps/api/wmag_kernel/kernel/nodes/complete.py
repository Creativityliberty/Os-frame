from __future__ import annotations
from typing import Any, Dict, List

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:  # minimal fallback so imports don't break without pocketflow
        pass
from kernel.runtime.events import status_event, artifact_event
from kernel.nodes._helpers import emit

class CompleteRunNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"task_id": shared["task"]["task_id"], "run_id": shared["run"]["run_id"], "step_results": shared.get("step_results") or []}

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        final = "completed"
        if any(r.get("status") == "failed" for r in prep_res["step_results"]):
            final = "failed"
        return {"final_state": final, "step_count": len(prep_res["step_results"])}

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        await emit(shared, artifact_event(prep_res["task_id"], prep_res["run_id"], "run_summary", exec_res))
        await emit(shared, status_event(prep_res["task_id"], prep_res["run_id"], exec_res["final_state"], "Done"))
        return "done"
