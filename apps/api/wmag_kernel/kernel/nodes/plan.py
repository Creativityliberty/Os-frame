from __future__ import annotations
from typing import Any, Dict
import math
import os

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:
        pass

from kernel.runtime.events import artifact_event, status_event
from kernel.runtime.validation import validate_plan
from kernel.nodes._helpers import emit


def _llm_cost_units_from_usage(limits: Dict[str, Any], usage: Dict[str, Any]) -> int:
    tokens = usage.get("total_token_count") or usage.get("estimated_total_tokens") or 0
    rate = int(limits.get("llm_cost_units_per_1k_tokens", limits.get("llm_call_cost_units", 10)))
    if tokens:
        return max(1, int(math.ceil((int(tokens) / 1000.0) * rate)))
    return int(limits.get("llm_call_cost_units", 10))


class PlanWithLLMNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {"task_id": shared["task"]["task_id"], "context_pack": shared.get("context_pack") or {}}

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        return prep_res

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        planner = shared["deps"]["planner"]
        storage = shared["deps"]["storage"]
        tenant = shared.get("tenant") or {}
        limits = (tenant.get("limits") or {}) if isinstance(tenant, dict) else {}

        plan = await planner.build_plan(exec_res["context_pack"])
        usage = getattr(planner, "last_usage", {}) or {}
        llm_cost = _llm_cost_units_from_usage(limits, usage)

        schema_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "schemas", "plan.schema.json"))
        validate_plan(plan, schema_path)

        # bank-grade: quotas + billing ledger
        try:
            model = getattr(planner, "model", "unknown")
            tokens = int(usage.get("total_token_count") or usage.get("estimated_total_tokens") or 0)
            if hasattr(storage, "consume_llm_quota"):
                await storage.consume_llm_quota(
                    tenant_ctx=tenant,
                    tenant_id=shared["task"].get("tenant_id"),
                    org_id=shared["task"].get("org_id"),
                    user_id=shared["task"].get("user_id"),
                    model=model,
                    tokens=tokens,
                    cost_units=llm_cost,
                    run_id=shared["run"]["run_id"],
                    kind="llm:build_plan",
                    meta={"usage": usage},
                )
        except Exception as e:
            await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "failed", "LLM quota exceeded", meta={"error": str(e)}))
            return "fatal"

        # budget accounting
        try:
            if hasattr(storage, "consume_budget"):
                await storage.consume_budget(shared["run"]["run_id"], prep_res["task_id"], {"llm_calls": 1, "cost_units": llm_cost}, limits, action_id="llm:build_plan")
        except Exception as e:
            await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "failed", "Budget exceeded", meta={"error": str(e)}))
            return "fatal"

        shared["plan"] = plan
        await emit(shared, artifact_event(prep_res["task_id"], shared["run"]["run_id"], "plan", {"plan": plan, "usage": usage}))
        return "default"
