from __future__ import annotations
from typing import Any, Dict, List

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:  # minimal fallback so imports don't break without pocketflow
        pass
from kernel.runtime.validation import validate_task_input
from kernel.runtime.events import status_event
from kernel.nodes._helpers import emit

class ReceiveTaskNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return shared["task"]

    async def exec_async(self, task: Dict[str, Any]) -> Dict[str, Any]:
        validate_task_input(task)
        return task

    async def post_async(self, shared: Dict[str, Any], task: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        storage = shared["deps"]["storage"]
        run = await storage.create_or_load_run(task_id=task["task_id"], tenant_id=task["tenant_id"])
        shared["run"] = run

        shared.setdefault("stream_queue", [])
        shared.setdefault("events_to_commit", [])

        await storage.set_run_state(run["run_id"], "submitted")
        await emit(shared, status_event(task["task_id"], run["run_id"], "submitted", "Task accepted"))

        await storage.set_run_state(run["run_id"], "working")
        await emit(shared, status_event(task["task_id"], run["run_id"], "working", "Running"))
        return "default"
