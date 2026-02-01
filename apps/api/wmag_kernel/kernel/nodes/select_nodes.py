from __future__ import annotations
from typing import Any, Dict, List
import math

try:
    from pocketflow import AsyncNode  # type: ignore
except Exception:  # pragma: no cover
    class AsyncNode:
        pass

from kernel.runtime.events import artifact_event, status_event
from kernel.nodes._helpers import emit


def _llm_cost_units_from_usage(limits: Dict[str, Any], usage: Dict[str, Any]) -> int:
    tokens = usage.get("total_token_count") or usage.get("estimated_total_tokens") or 0
    rate = int(limits.get("llm_cost_units_per_1k_tokens", limits.get("llm_call_cost_units", 5)))
    if tokens:
        return max(1, int(math.ceil((int(tokens) / 1000.0) * rate)))
    return int(limits.get("llm_call_cost_units", 5))


class SelectNodesTreeSearchNode(AsyncNode):
    async def prep_async(self, shared: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": shared["task"]["task_id"],
            "user_message": shared["task"]["user_message"],
            "trees": shared.get("trees") or [],
            "policies": (shared.get("registry") or {}).get("policies", []),
        }

    async def exec_async(self, prep_res: Dict[str, Any]) -> Dict[str, Any]:
        return prep_res

    async def post_async(self, shared: Dict[str, Any], prep_res: Dict[str, Any], exec_res: Dict[str, Any]) -> str:
        planner = shared["deps"]["planner"]
        storage = shared["deps"]["storage"]
        tenant = shared.get("tenant") or {}
        limits = (tenant.get("limits") or {}) if isinstance(tenant, dict) else {}

        node_list = await planner.select_nodes(exec_res["user_message"], exec_res["trees"], exec_res["policies"])
        usage = getattr(planner, "last_usage", {}) or {}
        llm_cost = _llm_cost_units_from_usage(limits, usage)

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
                    kind="llm:select_nodes",
                    meta={"usage": usage},
                )
        except Exception as e:
            await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "failed", "LLM quota exceeded", meta={"error": str(e)}))
            return "fatal"

        # budget accounting
        try:
            if hasattr(storage, "consume_budget"):
                await storage.consume_budget(shared["run"]["run_id"], prep_res["task_id"], {"llm_calls": 1, "cost_units": llm_cost}, limits, action_id="llm:select_nodes")
        except Exception as e:
            await emit(shared, status_event(prep_res["task_id"], shared["run"]["run_id"], "failed", "Budget exceeded", meta={"error": str(e)}))
            return "fatal"

        shared["node_selection"] = {"node_list": node_list, "confidence": 0.75, "usage": usage}
        await emit(shared, artifact_event(prep_res["task_id"], shared["run"]["run_id"], "node_selection", shared["node_selection"]))
        return "default"
