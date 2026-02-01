from __future__ import annotations
from typing import Any, Dict, List

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:  # minimal fallback so imports don't break without pocketflow
        pass
from kernel.runtime.events import status_event, artifact_event
from kernel.runtime.policy import policy_gate_plan
from kernel.nodes._helpers import emit

class ValidatePlanAndPolicyGateNode(AsyncNode):
    """CORE NODE: gate before any tool call."""

    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": shared["task"]["task_id"],
            "tenant": shared.get("tenant") or {},
            "registry": shared.get("registry") or {},
            "plan": shared.get("plan") or {},
        }

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        needs_approval, report = policy_gate_plan({"tenant": prep_res["tenant"], "registry": prep_res["registry"]}, prep_res["plan"])
        return {"needs_approval": needs_approval, "report": report}

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        shared["gate_report"] = exec_res["report"]
        await emit(shared, artifact_event(prep_res["task_id"], shared["run"]["run_id"], "gate_report", exec_res["report"]))

        if exec_res["report"].get("fatal"):
            await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "failed", "Policy gate failed", meta=exec_res["report"]))
            return "fatal"

        if exec_res["needs_approval"]:
            await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "input-required", "Approval required", meta=exec_res["report"]))
            return "need_approval"

        return "ok"
