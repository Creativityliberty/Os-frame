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
        self._seq: int = 0
        self.tenant_ctx = {
            "tenant_demo": {"tenant_id":"tenant_demo","roles":["support_agent"],"limits":{"max_tool_calls":50}},
            "tenant_enterprise_eu": {"tenant_id":"tenant_enterprise_eu","roles":["support_agent"],"limits":{"max_tool_calls":50}}
        }
        self.auto_approve = True

    async def create_or_load_run(self, task_id: str, tenant_id: str) -> Dict[str, Any]:
        run_id = self.runs.get(task_id, {}).get("run_id") or f"run_{task_id}"
        run = self.runs.get(task_id) or {"run_id": run_id, "task_id": task_id,
                "title": None,
                "tags": [],
                "budget_used": {"tool_calls": 0, "llm_calls": 0, "cost_units": 0}, "tenant_id": tenant_id, "state":"submitted"}
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
        self._seq += 1
        update = dict(update)
        update["_seq"] = self._seq
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

        tenant = await self.load_tenant_context(tenant_id)
        limits = tenant.get("limits", {}) or {}
        max_calls = int(limits.get("max_tool_calls", 50))
        per_tool = limits.get("per_tool", {}) or {}
        tool_counts: Dict[str, int] = {}
        tool_calls_used = 0

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

# Budget enforcement (tenant limits)
tool_calls_used += 1
tool_counts[tool] = tool_counts.get(tool, 0) + 1
if tool_calls_used > max_calls:
    err = {"class": "BUDGET", "message": f"max_tool_calls exceeded: {max_calls}"}
    sr = {"step_id": step_id, "status": "failed", "attempts": 1, "tool": tool, "action_id": action_id, "idempotency_key": idem_key, "error": err}
    await self.save_step_result(idem_key, sr)
    results.append(sr)
    return results
if tool in per_tool and tool_counts[tool] > int(per_tool[tool]):
    err = {"class": "BUDGET", "message": f"per_tool budget exceeded for {tool}: {per_tool[tool]}"}
    sr = {"step_id": step_id, "status": "failed", "attempts": 1, "tool": tool, "action_id": action_id, "idempotency_key": idem_key, "error": err}
    await self.save_step_result(idem_key, sr)
    results.append(sr)
    return results

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


# ---- API helpers (duck-typed) ----
async def save_task_input(self, run_id: str, task_input: Dict[str, Any]) -> None:
    # attach to run record
    for t, r in self.runs.items():
        if r["run_id"] == run_id:
            r["task_input"] = task_input
            return

async def find_run(self, run_id: str) -> Optional[Dict[str, Any]]:
    for t, r in self.runs.items():
        if r["run_id"] == run_id:
            return dict(r)
    return None

async def list_updates(self, run_id: str, since_seq: int | None = None, limit: int = 500) -> List[Dict[str, Any]]:
    since = since_seq or 0
    out = []
    for ev in self.updates.get(run_id, []):
        if int(ev.get("_seq", 0)) > since:
            out.append(ev)
    return out[:limit]

async def set_approval_decision(self, run_id: str, decision: Dict[str, Any]) -> None:
    approval_id = f"apr_{run_id}"
    self.approvals.setdefault(approval_id, {"approval_id": approval_id, "run_id": run_id, "payload": {}})
    self.approvals[approval_id]["decision"] = decision


async def list_runs(self, tenant_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    runs = [dict(r) for r in self.runs.values() if r.get("tenant_id") == tenant_id]
    # best-effort order: task_id
    runs.sort(key=lambda x: x.get("task_id",""), reverse=True)
    return runs[offset:offset+limit]

async def list_approvals(self, tenant_id: str, status: str = "pending", limit: int = 100) -> List[Dict[str, Any]]:
    out = []
    for ap in self.approvals.values():
        rid = ap.get("run_id")
        run = None
        for r in self.runs.values():
            if r.get("run_id") == rid:
                run = r
                break
        if not run or run.get("tenant_id") != tenant_id:
            continue
        decided = ap.get("decision") is not None
        if status == "pending" and decided:
            continue
        if status == "decided" and not decided:
            continue
        out.append(dict(ap))
    return out[:limit]


async def update_run_metadata(self, run_id: str, title: str | None = None, tags: list[str] | None = None) -> Dict[str, Any]:
    run = await self.find_run(run_id)
    if not run:
        raise KeyError("run not found")
    if title is not None:
        run["title"] = title
    if tags is not None:
        run["tags"] = tags
    return run

async def consume_budget(
    self,
    run_id: str,
    task_id: str,
    delta: Dict[str, int],
    limits: Dict[str, Any],
    tool: str | None = None,
    action_id: str | None = None,
) -> Dict[str, Any]:
    run = await self.find_run(run_id)
    if not run:
        raise KeyError("run not found")
    used = dict(run.get("budget_used") or {"tool_calls":0,"llm_calls":0,"cost_units":0})

    # apply caps
    max_tool_calls = int((limits.get("max_tool_calls") or 10**9))
    max_llm_calls = int((limits.get("max_llm_calls") or 10**9))
    max_cost_units = int((limits.get("max_cost_units") or 10**9))
    per_tool = limits.get("per_tool") or {}
    per_action = limits.get("per_action") or {}

    # counts
    if tool:
        used.setdefault("per_tool", {})
        used["per_tool"][tool] = int(used["per_tool"].get(tool, 0)) + int(delta.get("tool_calls", 0))
        if tool in per_tool and used["per_tool"][tool] > int(per_tool[tool]):
            raise RuntimeError(f"BUDGET per_tool exceeded for {tool}: {per_tool[tool]}")
    if action_id:
        used.setdefault("per_action", {})
        used["per_action"][action_id] = int(used["per_action"].get(action_id, 0)) + int(delta.get("cost_units", 0))
        if action_id in per_action and used["per_action"][action_id] > int(per_action[action_id]):
            raise RuntimeError(f"BUDGET per_action exceeded for {action_id}: {per_action[action_id]}")

    used["tool_calls"] = int(used.get("tool_calls", 0)) + int(delta.get("tool_calls", 0))
    used["llm_calls"] = int(used.get("llm_calls", 0)) + int(delta.get("llm_calls", 0))
    used["cost_units"] = int(used.get("cost_units", 0)) + int(delta.get("cost_units", 0))

    if used["tool_calls"] > max_tool_calls:
        raise RuntimeError(f"BUDGET max_tool_calls exceeded: {max_tool_calls}")
    if used["llm_calls"] > max_llm_calls:
        raise RuntimeError(f"BUDGET max_llm_calls exceeded: {max_llm_calls}")
    if used["cost_units"] > max_cost_units:
        raise RuntimeError(f"BUDGET max_cost_units exceeded: {max_cost_units}")

    run["budget_used"] = used
    return used


async def append_audit(self, record: Dict[str, Any]) -> None:
    # best effort: keep in memory
    self._audit = getattr(self, "_audit", [])
    self._audit.append(record)

async def upsert_snapshot(self, run_id: str) -> None:
    # in-memory: snapshot is the run itself
    return
