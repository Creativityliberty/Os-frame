from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Optional

from kernel.ports.storage import Storage
from kernel.ports.toolrunner import ToolRunner
from kernel.runtime.errors import classify_error
from kernel.runtime.retry import run_with_retry
from kernel.runtime.idempotency import compute_idempotency_key

class InMemoryStorage(Storage):
    def __init__(self):
        self.runs: Dict[str, Dict[str, Any]] = {}
        self.updates: Dict[str, List[Dict[str, Any]]] = {}
        self.step_cache: Dict[str, Dict[str, Any]] = {}
        self.approvals: Dict[str, Dict[str, Any]] = {}
        self.persist_calls: List[Dict[str, Any]] = []
        self.tenant_ctx = {
            "tenant_demo": {"tenant_id":"tenant_demo","roles":["support_agent"],"limits":{"max_tool_calls":50}},
            "tenant_enterprise_eu": {"tenant_id":"tenant_enterprise_eu","roles":["support_agent"],"limits":{"max_tool_calls":50}}
        }
        self.auto_approve = True

    async def create_or_load_run(self, task_id: str, tenant_id: str) -> Dict[str, Any]:
        run_id = self.runs.get(task_id, {}).get("run_id") or f"run_{task_id}"
        run = self.runs.get(task_id) or {"run_id": run_id, "task_id": task_id, "tenant_id": tenant_id, "state":"submitted"}
        self.runs[task_id] = run
        self.updates.setdefault(run_id, [])
        return run

    async def set_run_state(self, run_id: str, state: str) -> None:
        # find by run_id
        for t, r in self.runs.items():
            if r["run_id"] == run_id:
                r["state"] = state
                return

    async def persist_update(self, run_id: str, update: Dict[str, Any]) -> None:
        self.persist_calls.append({"run_id": run_id, "update": update})
        self.updates.setdefault(run_id, []).append(update)

    async def load_tenant_context(self, tenant_id: str) -> Dict[str, Any]:
        return dict(self.tenant_ctx.get(tenant_id, {"tenant_id":tenant_id,"roles":["support_agent"],"limits":{"max_tool_calls":50}}))

    async def create_approval_request(self, run_id: str, payload: Dict[str, Any]) -> str:
        approval_id = f"apr_{run_id}"
        self.approvals[approval_id] = {"approval_id": approval_id, "run_id": run_id, "payload": payload}
        return approval_id

    async def wait_for_approval(self, approval_id: str) -> Dict[str, Any]:
        # starter: always approve unless auto_approve False
        if self.auto_approve:
            return {"decision":"approved","by":"ops","ts":"now"}
        return {"decision":"denied","by":"ops","ts":"now"}

    async def get_step_result(self, idem_key: str) -> Optional[Dict[str, Any]]:
        return self.step_cache.get(idem_key)

    async def save_step_result(self, idem_key: str, step_result: Dict[str, Any]) -> None:
        self.step_cache[idem_key] = step_result

    # ---- Deterministic step executor ----
    async def execute_plan(self, run_id: str, task_input: Dict[str, Any], plan: Dict[str, Any], registry: Dict[str, Any], tools: ToolRunner) -> List[Dict[str, Any]]:
        tenant_id = task_input["tenant_id"]
        task_id = task_input["task_id"]

        # helpers
        def find_action(action_id: str) -> Dict[str, Any]:
            for a in registry.get("actions", []):
                if a.get("action_id") == action_id:
                    return a
            raise ValueError(f"Unknown action_id: {action_id}")

        def find_retry(rc_id: str) -> Dict[str, Any]:
            for rc in registry.get("retry_classes", []):
                if rc.get("id") == rc_id:
                    return rc
            return {"max_attempts":1,"backoff_ms":[],"retry_on":[]}

        # crude resolver for $sX.output.* references (starter)
        outputs: Dict[str, Dict[str, Any]] = {}
        results: List[Dict[str, Any]] = []

        async def resolve_args(args: Dict[str, Any]) -> Dict[str, Any]:
            # replace known patterns
            resolved = {}
            for k, v in args.items():
                if isinstance(v, str) and v.startswith("$s"):
                    m = v.split(".")
                    step_ref = m[0][1:]  # s3
                    field = m[-1]
                    resolved[k] = outputs.get(step_ref, {}).get(field)
                else:
                    resolved[k] = v
            return resolved

        for step in plan.get("steps", []):
            step_id = step["step_id"]
            action_id = step["action_id"]
            a = find_action(action_id)
            tool = a["tool"]
            timeout_ms = int(a.get("timeout_ms", 15000))
            retry_cfg = find_retry(a.get("retry_class","none"))

            raw_args = step.get("args") or {}
            args = await resolve_args(raw_args)

            idem_mode = (a.get("idempotency") or {}).get("mode", "hash_args")
            if idem_mode == "explicit_key":
                idem_key = args.get("idempotency_key")
                if not idem_key:
                    # hard fail in starter
                    result = {"step_id":step_id,"status":"failed","attempts":1,"tool":tool,"action_id":action_id,"error":{"class":"VALIDATION","message":"missing idempotency_key"}}
                    results.append(result)
                    return results
            else:
                idem_key = compute_idempotency_key(tenant_id=tenant_id, run_id=run_id, step_id=step_id, action_id=action_id, args=args)

            cached = await self.get_step_result(idem_key)
            if cached:
                results.append({**cached, "cache_hit": True})
                outputs[step_id] = cached.get("output") or {}
                continue

            async def tool_call():
                # timeout wrapper
                return await asyncio.wait_for(tools.call(tenant_id, tool, args), timeout=timeout_ms/1000)

            out, err, attempts = await run_with_retry(call=tool_call, classify_error=classify_error, retry_cfg=retry_cfg)
            if err:
                sr = {"step_id":step_id,"status":"failed","attempts":attempts,"tool":tool,"action_id":action_id,"idempotency_key": idem_key,"error":err}
                await self.save_step_result(idem_key, sr)
                results.append(sr)
                return results

            sr = {"step_id":step_id,"status":"succeeded","attempts":attempts,"tool":tool,"action_id":action_id,"idempotency_key": idem_key,"output":out}
            await self.save_step_result(idem_key, sr)
            results.append(sr)
            outputs[step_id] = out

            # optional crash simulation for replay test
            if task_input.get("metadata", {}).get("crash_after_step") == step_id:
                raise RuntimeError("simulated crash")

        return results
