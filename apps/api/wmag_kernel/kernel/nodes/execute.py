from __future__ import annotations
from typing import Any, Dict, List, Tuple

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:
        pass

from kernel.runtime.events import artifact_event, status_event
from kernel.nodes._helpers import emit


def _is_side_effect(step_result: Dict[str, Any]) -> bool:
    aid = str(step_result.get("action_id", "")).lower()
    tool = str(step_result.get("tool", "")).lower()
    if any(k in aid for k in ["send", "create", "write", "delete", "update", "charge", "refund"]):
        return True
    if any(k in tool for k in ["email", "gmail", "calendar", "crm"]):
        return True
    return False


def _collect_from_updates(updates: List[Dict[str, Any]]) -> Tuple[set, List[Dict[str, Any]]]:
    emitted_artifacts = set()
    step_results: List[Dict[str, Any]] = []
    for u in updates:
        if u.get("type") == "TaskArtifactUpdateEvent":
            art = u.get("artifact") or {}
            if isinstance(art, dict) and art.get("type"):
                emitted_artifacts.add(str(art["type"]))
            if isinstance(art, dict) and art.get("type") == "step_result":
                payload = art.get("payload") or {}
                if isinstance(payload, dict):
                    step_results.append(payload)
    return emitted_artifacts, step_results


def _check_obligations(obligations: List[Dict[str, Any]], emitted_artifacts: set, step_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    failures = []
    for ob in obligations:
        if not isinstance(ob, dict):
            continue
        if ob.get("type") == "must_emit_artifact":
            needed = ob.get("artifact_type")
            if needed and str(needed) not in emitted_artifacts:
                failures.append({"obligation": ob, "reason": f"missing artifact '{needed}'"})
        if ob.get("type") == "must_reference_policy_id":
            required = ob.get("policy_id")
            if required:
                # side effects must carry required policy id in policy_ids
                bad = []
                for sr in step_results:
                    if _is_side_effect(sr):
                        pids = sr.get("policy_ids") or []
                        if str(required) not in [str(x) for x in pids]:
                            bad.append({"step_id": sr.get("step_id"), "action_id": sr.get("action_id"), "policy_ids": pids})
                if bad:
                    failures.append({"obligation": ob, "reason": "side-effect steps missing policy reference", "violations": bad[:20]})
    return failures


class ExecuteStepsLoopNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": shared["task"]["task_id"],
            "run_id": shared["run"]["run_id"],
            "task": shared["task"],
            "plan": shared.get("plan") or {},
            "registry": shared.get("registry") or {},
        }

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        return prep_res

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        storage = shared["deps"]["storage"]
        tools = shared["deps"]["tools"]

        step_results = await storage.execute_plan(exec_res["run_id"], exec_res["task"], exec_res["plan"], exec_res["registry"], tools)
        shared["step_results"] = step_results

        for r in step_results:
            await emit(shared, artifact_event(prep_res["task_id"], exec_res["run_id"], "step_result", r))

        if any(r.get("status") == "failed" for r in step_results):
            await emit(shared, status_event(prep_res["task_id"], exec_res["run_id"], "failed", "Execution failed"))
            return "failed"

        obligations = (exec_res.get("plan") or {}).get("obligations") or []
        if obligations:
            updates = await storage.list_updates(exec_res["run_id"], since_seq=0)
            emitted, step_results_from_stream = _collect_from_updates(updates)
            failures = _check_obligations(obligations, emitted, step_results_from_stream)
            if failures:
                await emit(shared, artifact_event(prep_res["task_id"], exec_res["run_id"], "policy_obligations_failed", {"failures": failures}))
                await emit(shared, status_event(prep_res["task_id"], exec_res["run_id"], "failed", "Policy obligations failed"))
                return "failed"

        await emit(shared, status_event(prep_res["task_id"], exec_res["run_id"], "succeeded", "Done"))
        return "ok"
