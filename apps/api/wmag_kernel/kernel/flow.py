from __future__ import annotations
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict

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
    """WMAG Kernel runtime.

    This class exposes a single streaming entrypoint:
    - task_send_subscribe(task_input) -> async iterator of SSE-ish dict events

    It supports two execution modes:
    1) PocketFlow mode (preferred): runs Node graph and yields events from shared.stream_queue
    2) Legacy mode (fallback): runs the original sequential pipeline

    PocketFlow mode is selected automatically when `pocketflow` is installed.
    """

    storage: Storage
    registry: RegistryProvider
    index: WorldIndexProvider
    planner: LLMPlanner
    hydrator: ContextHydrator
    tools: ToolRunner

    async def task_send_subscribe(self, task_input: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        """Streaming task entrypoint.

        Persist-before-send is enforced by:
        - PocketFlow nodes: `emit(shared, ev)` persists then enqueues
        - Legacy pipeline: persist_update before every yield
        """
        validate_task_input(task_input)

        # Try PocketFlow mode first
        try:
            from kernel.pocketflow_flow import build_flow
            from kernel.pocketflow_runner import run_streaming
        except Exception:
            # pocketflow not available or wiring missing
            async for ev in self._task_send_subscribe_legacy(task_input):
                yield ev
            return

        try:
            flow = build_flow()
        except Exception:
            # pocketflow not installed or cannot build graph
            async for ev in self._task_send_subscribe_legacy(task_input):
                yield ev
            return

        shared: Dict[str, Any] = {
            "task": task_input,
            "deps": {
                "storage": self.storage,
                "registry": self.registry,
                "index": self.index,
                "planner": self.planner,
                "hydrator": self.hydrator,
                "tools": self.tools,
            },
            "stream_queue": [],
        }

        async for ev in run_streaming(flow.start, shared):
            yield ev

    async def _task_send_subscribe_legacy(self, task_input: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        """Legacy sequential pipeline (kept for compatibility + as fallback)."""

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
        reg = await self.registry.load_registry_for(task_input) if hasattr(self.registry, "load_registry_for") else await self.registry.load_registry(task_input["tenant_id"])
        trees = await self.index.load_or_build_trees(task_input["tenant_id"], ["support", "customers"])
        # budgets: LLM select_nodes
llm_cost = int((tenant_ctx.get("limits", {}) or {}).get("llm_call_cost_units", 5))
if hasattr(self.storage, "consume_budget"):
    await self.storage.consume_budget(run_id, task_input["task_id"], {"llm_calls": 1, "cost_units": llm_cost}, tenant_ctx.get("limits", {}) or {}, action_id="llm:select_nodes")
node_list = await self.planner.select_nodes(task_input["user_message"], trees, reg.get("policies", []))
        pack = await self.hydrator.hydrate(task_input["tenant_id"], task_input["user_message"], node_list, reg)
        llm_cost = int((tenant_ctx.get("limits", {}) or {}).get("llm_call_cost_units", 10))
if hasattr(self.storage, "consume_budget"):
    await self.storage.consume_budget(run_id, task_input["task_id"], {"llm_calls": 1, "cost_units": llm_cost}, tenant_ctx.get("limits", {}) or {}, action_id="llm:build_plan")
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
