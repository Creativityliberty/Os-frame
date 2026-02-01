from __future__ import annotations
from typing import Any, Dict, List

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:  # minimal fallback so imports don't break without pocketflow
        pass
from kernel.runtime.events import status_event, artifact_event
from kernel.nodes._helpers import emit

class RequestApprovalNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"task_id": shared["task"]["task_id"], "plan": shared.get("plan") or {}, "gate_report": shared.get("gate_report") or {}}

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        storage = shared["deps"]["storage"]
        approval_id = await storage.create_approval_request(shared["run"]["run_id"], {"plan": prep_res["plan"], "report": prep_res["gate_report"]})
        return {"approval_id": approval_id}

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        shared["approval_id"] = exec_res["approval_id"]
        await emit(shared, artifact_event(prep_res["task_id"], shared["run"]["run_id"], "approval_request", {"approval_id": exec_res["approval_id"]}))
        return "default"

class WaitForApprovalNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"task_id": shared["task"]["task_id"], "approval_id": shared.get("approval_id")}

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        storage = shared["deps"]["storage"]
        return await storage.wait_for_approval(prep_res["approval_id"])

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        decision = exec_res.get("decision")
        if decision == "approved":
            await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "working", "Approved, continuing"))
            return "approved"
        if decision == "denied":
            await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "canceled", "Approval denied"))
            return "denied"
        await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "canceled", "Approval timeout/unknown"))
        return "timeout"
