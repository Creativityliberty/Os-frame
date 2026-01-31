from __future__ import annotations
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List

from kernel.ports.storage import Storage
from kernel.ports.registry import RegistryProvider
from kernel.ports.index import WorldIndexProvider
from kernel.ports.planner import LLMPlanner
from kernel.ports.hydrator import ContextHydrator
from kernel.ports.toolrunner import ToolRunner

from kernel.runtime.events import status_event, artifact_event
from kernel.runtime.validation import validate_task_input
from kernel.runtime.policy import policy_gate_plan

@dataclass
class Kernel:
    storage: Storage
    registry: RegistryProvider
    index: WorldIndexProvider
    planner: LLMPlanner
    hydrator: ContextHydrator
    tools: ToolRunner

    async def task_send_subscribe(self, task_input: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        validate_task_input(task_input)

        run = await self.storage.create_or_load_run(task_id=task_input["task_id"], tenant_id=task_input["tenant_id"])
        run_id = run["run_id"]

        ev = status_event(task_input["task_id"], run_id, "submitted", "Task accepted")
        await self.storage.persist_update(run_id, ev)
        yield ev

        await self.storage.set_run_state(run_id, "working")
        ev = status_event(task_input["task_id"], run_id, "working", "Running")
        await self.storage.persist_update(run_id, ev)
        yield ev

        tenant_ctx = await self.storage.load_tenant_context(task_input["tenant_id"])
        reg = await self.registry.load_registry(task_input["tenant_id"])
        trees = await self.index.load_or_build_trees(task_input["tenant_id"], ["support", "customers"])
        node_list = await self.planner.select_nodes(task_input["user_message"], trees, reg.get("policies", []))
        pack = await self.hydrator.hydrate(task_input["tenant_id"], task_input["user_message"], node_list, reg)
        plan = await self.planner.build_plan(pack)

        ev = artifact_event(task_input["task_id"], run_id, "plan", plan)
        await self.storage.persist_update(run_id, ev)
        yield ev

        needs_approval, report = policy_gate_plan({"tenant": tenant_ctx, "registry": reg}, plan)
        if report.get("fatal"):
            ev = status_event(task_input["task_id"], run_id, "failed", "Policy gate failed", meta=report)
            await self.storage.persist_update(run_id, ev)
            yield ev
            return

        if needs_approval:
            ev = status_event(task_input["task_id"], run_id, "input-required", "Approval required", meta=report)
            await self.storage.persist_update(run_id, ev)
            yield ev

            approval_id = await self.storage.create_approval_request(run_id, {"plan": plan, "report": report})
            decision = await self.storage.wait_for_approval(approval_id)
            if decision.get("decision") != "approved":
                ev = status_event(task_input["task_id"], run_id, "canceled", "Approval denied")
                await self.storage.persist_update(run_id, ev)
                yield ev
                return

            ev = status_event(task_input["task_id"], run_id, "working", "Approved, continuing")
            await self.storage.persist_update(run_id, ev)
            yield ev

        # execute deterministic
        try:
            step_results = await self.storage.execute_plan(run_id, task_input, plan, reg, self.tools)
        except Exception as exc:
            # crash -> mark failed (replay test will rerun with idempotency cache)
            ev = status_event(task_input["task_id"], run_id, "failed", f"Kernel crashed: {exc}")
            await self.storage.persist_update(run_id, ev)
            yield ev
            return

        for r in step_results:
            ev = artifact_event(task_input["task_id"], run_id, "step_result", r)
            await self.storage.persist_update(run_id, ev)
            yield ev

        final_state = "failed" if any(r.get("status") == "failed" for r in step_results) else "completed"
        ev = status_event(task_input["task_id"], run_id, final_state, "Done")
        await self.storage.persist_update(run_id, ev)
        yield ev
