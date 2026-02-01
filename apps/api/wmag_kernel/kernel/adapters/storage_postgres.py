from __future__ import annotations

import asyncio
import json
import os
import hashlib
import hmac
import datetime
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from kernel.ports.storage import Storage
from kernel.ports.toolrunner import ToolRunner
from kernel.runtime.errors import classify_error
from kernel.runtime.retry import run_with_retry
from kernel.runtime.idempotency import compute_idempotency_key


SNAPSHOT_EVERY = int(os.getenv("SNAPSHOT_EVERY", "25"))
REFRESH_MV_EVERY = int(os.getenv("REFRESH_MV_EVERY", "50"))  # refresh mviews every N events (best effort)

INIT_SQL = """CREATE TABLE IF NOT EXISTS runs (
  task_id TEXT PRIMARY KEY,
  run_id TEXT UNIQUE NOT NULL,
  tenant_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'submitted',
  title TEXT,
  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  budget_used JSONB NOT NULL DEFAULT '{}'::jsonb,
  task_input JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_events (
  id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  seq BIGINT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  event JSONB NOT NULL,
  canonical JSONB NOT NULL,
  prev_hash TEXT,
  key_id TEXT,
  hash TEXT,
  key_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_run_seq ON run_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_run_events_run_id_id ON run_events(run_id, id);

CREATE TABLE IF NOT EXISTS step_cache (
  idem_key TEXT PRIMARY KEY,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  payload JSONB NOT NULL,
  decision JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  record JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage_daily (
  scope TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  day DATE NOT NULL,
  model TEXT NOT NULL,
  tokens BIGINT NOT NULL DEFAULT 0,
  cost_units BIGINT NOT NULL DEFAULT 0,
  calls BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (scope, scope_id, day, model)
);

CREATE TABLE IF NOT EXISTS audit_keys (
  kid TEXT PRIMARY KEY,
  secret TEXT NOT NULL,
  active BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_keys_active ON audit_keys(active);

CREATE TABLE IF NOT EXISTS billing_ledger (
  ledger_id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  tenant_id TEXT NOT NULL,
  org_id TEXT,
  user_id TEXT,
  run_id TEXT,
  kind TEXT NOT NULL,
  model TEXT,
  tokens BIGINT,
  cost_units BIGINT,
  meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_billing_tenant_ts ON billing_ledger(tenant_id, ts);

CREATE TABLE IF NOT EXISTS run_snapshots (
  run_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  last_seq BIGINT NOT NULL DEFAULT 0,
  state TEXT,
  title TEXT,
  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  budget_used JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _canonical_json(ev: Dict[str, Any]) -> str:
    return json.dumps(ev, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def _load_audit_keyring() -> Dict[str, Any]:
    """Load keyring from env.

    Preferred: AUDIT_KEYS_JSON='[{"kid":"k1","secret":"...","active":true},{"kid":"k0","secret":"...","active":false}]'

    Fallback: AUDIT_SECRET -> single key kid="k0" active=true
    """
    raw = os.getenv("AUDIT_KEYS_JSON", "").strip()
    if raw:
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                keys = []
                for k in arr:
                    if isinstance(k, dict) and k.get("kid") and k.get("secret"):
                        keys.append({"kid": str(k["kid"]), "secret": str(k["secret"]), "active": bool(k.get("active", False))})
                if keys:
                    active = next((k for k in keys if k.get("active")), keys[0])
                    return {"keys": keys, "active_kid": active["kid"]}
        except Exception:
            pass
    # fallback single key
    secret = os.getenv("AUDIT_SECRET", "dev_audit_secret_change_me")
    return {"keys": [{"kid":"k0","secret":secret,"active":True}], "active_kid":"k0"}

_KEYRING = _load_audit_keyring()

def _get_key(kid: str) -> str:
    for k in _KEYRING.get("keys", []):
        if k.get("kid") == kid:
            return str(k.get("secret"))
    # unknown kid -> fallback to active
    return _get_key(str(_KEYRING.get("active_kid","k0")))

def _active_kid() -> str:
    return str(_KEYRING.get("active_kid","k0"))

def _hash(prev_hash: str, canonical_json: str, kid: str) -> str:
    secret = _get_key(kid).encode("utf-8")
    msg = ((prev_hash or "") + "|" + canonical_json).encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


class PostgresStorage(Storage):
    """Production storage: Postgres event log + step cache + approvals + budgets + snapshots."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        if self._pool is not None:
            return
        min_size = int(os.getenv("PG_POOL_MIN", "1"))
        max_size = int(os.getenv("PG_POOL_MAX", "10"))
        self._pool = await asyncpg.create_pool(dsn=self.dsn, min_size=min_size, max_size=max_size)
        async with self._pool.acquire() as con:
            await con.execute(INIT_SQL)
            # materialized views (optional)
            await self._ensure_materialized_views(con)
            await self.seed_audit_keys()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgresStorage not initialized. Call await storage.init() at startup.")
        return self._pool

    async def _ensure_materialized_views(self, con: asyncpg.Connection) -> None:
        # Runs MV
        await con.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS runs_mv AS
        SELECT
          r.run_id, r.task_id, r.tenant_id,
          COALESCE(s.state, r.state) AS state,
          COALESCE(s.title, r.title) AS title,
          COALESCE(s.tags, r.tags) AS tags,
          COALESCE(s.budget_used, r.budget_used) AS budget_used,
          COALESCE(s.updated_at, r.updated_at) AS updated_at,
          r.created_at AS created_at
        FROM runs r
        LEFT JOIN run_snapshots s ON s.run_id = r.run_id;
        """)
        await con.execute("CREATE UNIQUE INDEX IF NOT EXISTS runs_mv_run_id_uidx ON runs_mv(run_id)")
        await con.execute("CREATE INDEX IF NOT EXISTS runs_mv_tenant_state_idx ON runs_mv(tenant_id, state, updated_at)")

        # Approvals MV
        await con.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS approvals_mv AS
        SELECT
          approval_id, run_id,
          COALESCE(decision->>'decision','pending') AS decision,
          created_at, decided_at
        FROM approvals;
        """)
        await con.execute("CREATE UNIQUE INDEX IF NOT EXISTS approvals_mv_id_uidx ON approvals_mv(approval_id)")

async def refresh_materialized_views(self, concurrently: bool = False) -> None:
    await self.init()
    async with self.pool.acquire() as con:
        try:
            if concurrently:
                await con.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY runs_mv")
            else:
                await con.execute("REFRESH MATERIALIZED VIEW runs_mv")
        except Exception:
            pass
        try:
            if concurrently:
                await con.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY approvals_mv")
            else:
                await con.execute("REFRESH MATERIALIZED VIEW approvals_mv")
        except Exception:
            pass
    async def create_or_load_run(self, task_id: str, tenant_id: str) -> Dict[str, Any]:
        await self.init()
        run_id = f"run_{task_id}"
        async with self.pool.acquire() as con:
            row = await con.fetchrow("SELECT run_id, task_id, tenant_id, state, title, tags, budget_used, task_input FROM runs WHERE task_id=$1", task_id)
            if row:
                return dict(row)
            await con.execute(
                "INSERT INTO runs(task_id, run_id, tenant_id, state) VALUES($1,$2,$3,'submitted')",
                task_id, run_id, tenant_id
            )
            row = await con.fetchrow("SELECT run_id, task_id, tenant_id, state, title, tags, budget_used, task_input FROM runs WHERE task_id=$1", task_id)
            return dict(row)

    async def set_run_state(self, run_id: str, state: str) -> None:
        await self.init()
        async with self.pool.acquire() as con:
            await con.execute("UPDATE runs SET state=$1, updated_at=now() WHERE run_id=$2", state, run_id)
        await self.upsert_snapshot(run_id)

    async def persist_update(self, run_id: str, update: Dict[str, Any]) -> None:
        await self.init()
        async with self.pool.acquire() as con:
            async with con.transaction():
                last = await con.fetchrow("SELECT COALESCE(MAX(seq),0) AS seq, COALESCE(MAX(hash),'') AS hash FROM run_events WHERE run_id=$1", run_id)
                seq = int(last["seq"]) + 1
                prev_hash = str(last["hash"] or "")
                update2 = dict(update)
                update2["_seq"] = seq
                canonical = update2  # keep deterministic
                canonical_str = _canonical_json(canonical)
                kid = _active_kid()
                h = _hash(prev_hash, canonical_str, kid)
                await con.execute(
                    "INSERT INTO run_events(run_id, seq, event, canonical, prev_hash, h, kidash, key_id) VALUES($1,$2,$3::jsonb,$4::jsonb,$5,$6,$7)",
                    run_id, seq, json.dumps(update2, ensure_ascii=False), json.dumps(canonical, ensure_ascii=False),
                    prev_hash, h, kid
                )

        # snapshot + optional MV refresh best-effort
        if seq % SNAPSHOT_EVERY == 0:
            await self.upsert_snapshot(run_id)
        if seq % REFRESH_MV_EVERY == 0:
            try:
                await self.refresh_materialized_views()
            except Exception:
                pass

    async def list_updates(self, run_id: str, since_seq: int = 0) -> List[Dict[str, Any]]:
        await self.init()
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT seq, event FROM run_events WHERE run_id=$1 AND seq > $2 ORDER BY seq ASC LIMIT 5000",
                run_id, int(since_seq)
            )
        out = []
        for r in rows:
            ev = dict(r["event"])
            ev["_seq"] = int(r["seq"])
            out.append(ev)
        return out

    async def find_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        await self.init()
        async with self.pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT r.run_id, r.task_id, r.tenant_id, r.state, r.title, r.tags, r.budget_used, r.task_input, r.created_at, r.updated_at, "
                "s.last_seq, s.updated_at AS snapshot_updated_at "
                "FROM runs r LEFT JOIN run_snapshots s ON s.run_id=r.run_id WHERE r.run_id=$1",
                run_id
            )
        return dict(row) if row else None

    async def save_task_input(self, run_id: str, task_input: Dict[str, Any]) -> None:
        await self.init()
        async with self.pool.acquire() as con:
            await con.execute("UPDATE runs SET task_input=$1::jsonb, updated_at=now() WHERE run_id=$2", json.dumps(task_input, ensure_ascii=False), run_id)
        await self.upsert_snapshot(run_id)

    async def update_run_metadata(self, run_id: str, title: Optional[str] = None, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        await self.init()
        async with self.pool.acquire() as con:
            if title is not None:
                await con.execute("UPDATE runs SET title=$1, updated_at=now() WHERE run_id=$2", title, run_id)
            if tags is not None:
                await con.execute("UPDATE runs SET tags=$1::jsonb, updated_at=now() WHERE run_id=$2", json.dumps(tags, ensure_ascii=False), run_id)
        await self.upsert_snapshot(run_id)
        return (await self.find_run(run_id)) or {}

    async def upsert_snapshot(self, run_id: str) -> None:
        await self.init()
        async with self.pool.acquire() as con:
            run = await con.fetchrow("SELECT run_id, tenant_id, state, title, tags, budget_used, updated_at FROM runs WHERE run_id=$1", run_id)
            if not run:
                return
            last_seq = await con.fetchval("SELECT COALESCE(MAX(seq),0) FROM run_events WHERE run_id=$1", run_id)
            await con.execute(
                """
                INSERT INTO run_snapshots(run_id, tenant_id, last_seq, state, title, tags, budget_used, updated_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,NOW())
                ON CONFLICT (run_id) DO UPDATE
                  SET last_seq=EXCLUDED.last_seq, state=EXCLUDED.state, title=EXCLUDED.title,
                      tags=EXCLUDED.tags, budget_used=EXCLUDED.budget_used, updated_at=NOW()
                """,
                run["run_id"], run["tenant_id"], int(last_seq),
                run["state"], run["title"],
                json.dumps(run["tags"] or [], ensure_ascii=False),
                json.dumps(run["budget_used"] or {}, ensure_ascii=False),
            )

    async def list_runs(self, tenant_id: str, limit: int = 50, offset: int = 0, query: Optional[str] = None, state: Optional[str] = None, tag: Optional[str] = None) -> List[Dict[str, Any]]:
        await self.init()
        q = "SELECT * FROM runs_mv WHERE tenant_id=$1"
        params: List[Any] = [tenant_id]
        i = 2

        if state:
            q += f" AND state=${i}"
            params.append(state)
            i += 1
        if tag:
            q += f" AND tags ? ${i}"
            params.append(tag)
            i += 1
        if query:
            q += f" AND (run_id ILIKE ${i} OR task_id ILIKE ${i} OR COALESCE(title,'') ILIKE ${i})"
            params.append(f"%{query}%")
            i += 1

        q += " ORDER BY updated_at DESC LIMIT $%d OFFSET $%d" % (i, i+1)
        params.append(int(limit))
        params.append(int(offset))

        async with self.pool.acquire() as con:
            rows = await con.fetch(q, *params)
        return [dict(r) for r in rows]

    # -------- Tenant context --------

    async def load_tenant_context(self, tenant_id: str) -> Dict[str, Any]:
        # File-based tenant context. (For real prod: move to DB/config service.)
        from pathlib import Path
        cfg_dir = Path(os.getenv("TENANT_CONFIG_DIR", "./config/tenants"))
        candidates = [
            cfg_dir / tenant_id / f"{tenant_id}.json",
            cfg_dir / tenant_id / "tenant.json",
            cfg_dir / f"{tenant_id}.json",
        ]
        for p in candidates:
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return {"tenant_id": tenant_id, "roles": ["support_agent"], "limits": {"max_tool_calls": 50}, "rate_limits": {"tenant_rpm": 600, "user_rpm": 120, "org_rpm": 600}}

    # -------- Approvals --------

    async def create_approval_request(self, run_id: str, payload: Dict[str, Any]) -> str:
        await self.init()
        approval_id = f"apr_{run_id}"
        async with self.pool.acquire() as con:
            await con.execute(
                "INSERT INTO approvals(approval_id, run_id, payload) VALUES($1,$2,$3::jsonb) "
                "ON CONFLICT (approval_id) DO UPDATE SET payload=EXCLUDED.payload",
                approval_id, run_id, json.dumps(payload, ensure_ascii=False)
            )
        try:
            await self.refresh_materialized_views()
        except Exception:
            pass
        return approval_id

    async def set_approval_decision(self, run_id: str, decision: Dict[str, Any]) -> None:
        await self.init()
        approval_id = f"apr_{run_id}"
        async with self.pool.acquire() as con:
            await con.execute(
                "UPDATE approvals SET decision=$1::jsonb, decided_at=now() WHERE approval_id=$2",
                json.dumps(decision, ensure_ascii=False), approval_id
            )
        try:
            await self.refresh_materialized_views()
        except Exception:
            pass

    async def list_approvals(self, tenant_id: str, status: str = "pending", limit: int = 100) -> List[Dict[str, Any]]:
        await self.init()
        # approvals are by run tenant, so join runs
        cond = "decision IS NULL" if status == "pending" else "decision IS NOT NULL"
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                f"SELECT a.approval_id, a.run_id, a.payload, a.decision, a.created_at, a.decided_at "
                f"FROM approvals a JOIN runs r ON r.run_id=a.run_id WHERE r.tenant_id=$1 AND {cond} "
                f"ORDER BY a.created_at DESC LIMIT $2",
                tenant_id, int(limit)
            )
        return [dict(r) for r in rows]

    async def wait_for_approval(self, approval_id: str) -> Dict[str, Any]:
        await self.init()
        deadline = asyncio.get_event_loop().time() + 3600
        while True:
            async with self.pool.acquire() as con:
                row = await con.fetchrow("SELECT decision FROM approvals WHERE approval_id=$1", approval_id)
                if row and row["decision"] is not None:
                    return dict(row["decision"])
            if asyncio.get_event_loop().time() > deadline:
                return {"decision": "denied", "by": "system", "ts": "timeout"}
            await asyncio.sleep(0.5)

    # -------- Step cache --------

    async def get_step_result(self, idem_key: str) -> Optional[Dict[str, Any]]:
        await self.init()
        async with self.pool.acquire() as con:
            row = await con.fetchrow("SELECT payload FROM step_cache WHERE idem_key=$1", idem_key)
        return dict(row["payload"]) if row else None

    async def save_step_result(self, idem_key: str, step_result: Dict[str, Any]) -> None:
        await self.init()
        async with self.pool.acquire() as con:
            await con.execute(
                "INSERT INTO step_cache(idem_key, payload) VALUES($1,$2::jsonb) "
                "ON CONFLICT (idem_key) DO UPDATE SET payload=EXCLUDED.payload",
                idem_key, json.dumps(step_result, ensure_ascii=False)
            )

    # -------- Budgets --------

    async def consume_budget(self, run_id: str, task_id: str, delta: Dict[str, int], limits: Dict[str, Any], action_id: str = "") -> Dict[str, Any]:
        """Atomic budget check+increment on runs.budget_used."""
        await self.init()
        max_tool = int((limits or {}).get("max_tool_calls", 10**9))
        max_llm = int((limits or {}).get("max_llm_calls", 10**9))
        max_cost = int((limits or {}).get("max_cost_units", 10**9))

        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow("SELECT budget_used FROM runs WHERE run_id=$1 FOR UPDATE", run_id)
                used = dict(row["budget_used"] or {})
                tool_calls = int(used.get("tool_calls", 0)) + int(delta.get("tool_calls", 0))
                llm_calls = int(used.get("llm_calls", 0)) + int(delta.get("llm_calls", 0))
                cost_units = int(used.get("cost_units", 0)) + int(delta.get("cost_units", 0))

                if tool_calls > max_tool:
                    raise RuntimeError("budget exceeded: max_tool_calls")
                if llm_calls > max_llm:
                    raise RuntimeError("budget exceeded: max_llm_calls")
                if cost_units > max_cost:
                    raise RuntimeError("budget exceeded: max_cost_units")

                used2 = {"tool_calls": tool_calls, "llm_calls": llm_calls, "cost_units": cost_units}
                await con.execute("UPDATE runs SET budget_used=$1::jsonb, updated_at=now() WHERE run_id=$2", json.dumps(used2, ensure_ascii=False), run_id)

        return {"used": used2, "limits": limits}

    # -------- Deterministic step executor --------

    async def execute_plan(self, run_id: str, task_input: Dict[str, Any], plan: Dict[str, Any], registry: Dict[str, Any], tools: ToolRunner) -> List[Dict[str, Any]]:
        tenant_id = task_input["tenant_id"]
        task_id = task_input["task_id"]

        tenant = await self.load_tenant_context(tenant_id)
        limits = (tenant.get("limits", {}) or {})
        max_calls = int(limits.get("max_tool_calls", 50))
        per_tool = (limits.get("per_tool", {}) or {})
        per_action = (limits.get("per_action", {}) or {})
        tool_counts: Dict[str, int] = {}
        tool_calls_used = 0

        def _is_side_effect_action(action: Dict[str, Any], action_id: str, tool: str) -> bool:
            if isinstance(action, dict) and action.get("side_effect") is True:
                return True
            lowered = (action_id or "").lower()
            if any(k in lowered for k in ["send", "create", "write", "delete", "update", "charge", "refund"]):
                return True
            if tool and any(k in tool.lower() for k in ["email", "gmail", "calendar", "crm"]):
                return True
            return False

        def find_action(action_id: str) -> Dict[str, Any]:
            for a in registry.get("actions", []):
                if a.get("action_id") == action_id:
                    return a
            raise ValueError(f"Unknown action_id: {action_id}")

        def find_retry(rc_id: str) -> Dict[str, Any]:
            for rc in registry.get("retry_classes", []):
                if rc.get("id") == rc_id:
                    return rc
            return {"max_attempts": 1, "backoff_ms": [], "retry_on": []}

        outputs: Dict[str, Dict[str, Any]] = {}
        results: List[Dict[str, Any]] = []

        async def resolve_args(args: Dict[str, Any]) -> Dict[str, Any]:
            resolved = {}
            for k, v in (args or {}).items():
                if isinstance(v, str) and v.startswith("$s"):
                    m = v.split(".")
                    step_ref = m[0][1:]  # s3
                    field = m[-1]
                    resolved[k] = (outputs.get(step_ref, {}) or {}).get(field)
                else:
                    resolved[k] = v
            return resolved

        for step in (plan.get("steps") or []):
            step_id = step["step_id"]
            action_id = step["action_id"]
            a = find_action(action_id)
            tool = a["tool"]
            timeout_ms = int(a.get("timeout_ms", 15000))
            retry_cfg = find_retry(a.get("retry_class", "none"))
            cost_units = int(step.get("cost_units_override") or a.get("cost_units", 1))

            requires_approval = bool(step.get("requires_approval") or a.get("requires_approval"))
            side_effect = _is_side_effect_action(a, action_id, tool)

            if side_effect:
                ak = (step.get("args") or {}).get("idempotency_key")
                if not ak:
                    results.append({
                        "step_id": step_id, "action_id": action_id, "tool": tool,
                        "status": "failed",
                        "error": {"class": "IDEMPOTENCY", "message": "missing args.idempotency_key for side-effect action"},
                        "output": {},
                        "policy_ids": step.get("policy_ids") or [],
                    })
                    continue

            if requires_approval:
                approval_id = await self.create_approval_request(run_id, {"step_id": step_id, "action_id": action_id, "tool": tool, "args": step.get("args", {})})
                decision = await self.wait_for_approval(approval_id)
                if decision.get("decision") != "approved":
                    res = {"step_id": step_id, "action_id": action_id, "tool": tool, "status": "failed",
                           "error": {"class": "APPROVAL_DENIED", "message": "approval denied"}, "output": {}, "policy_ids": step.get("policy_ids") or []}
                    results.append(res)
                    continue

            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            tool_calls_used += 1
            if tool_calls_used > max_calls:
                results.append({"step_id": step_id, "action_id": action_id, "tool": tool, "status": "failed",
                                "error": {"class": "BUDGET", "message": "max tool calls exceeded"}, "output": {}, "policy_ids": step.get("policy_ids") or []})
                continue
            if tool in per_tool and tool_counts[tool] > int(per_tool[tool]):
                results.append({"step_id": step_id, "action_id": action_id, "tool": tool, "status": "failed",
                                "error": {"class": "BUDGET", "message": "per-tool budget exceeded"}, "output": {}, "policy_ids": step.get("policy_ids") or []})
                continue
            if action_id in per_action and tool_counts[tool] > int(per_action[action_id]):
                results.append({"step_id": step_id, "action_id": action_id, "tool": tool, "status": "failed",
                                "error": {"class": "BUDGET", "message": "per-action budget exceeded"}, "output": {}, "policy_ids": step.get("policy_ids") or []})
                continue

            if hasattr(self, "consume_budget"):
                try:
                    await self.consume_budget(run_id, task_id, {"tool_calls": 1, "cost_units": cost_units}, limits, action_id=action_id)
                except Exception as e:
                    results.append({"step_id": step_id, "action_id": action_id, "tool": tool, "status": "failed",
                                    "error": {"class": "BUDGET", "message": str(e)}, "output": {}, "policy_ids": step.get("policy_ids") or []})
                    continue

            args = await resolve_args(step.get("args") or {})
            idem_key = compute_idempotency_key(task_id, step_id, action_id, args)
            cached = await self.get_step_result(idem_key)
            if cached:
                results.append(cached)
                outputs[step_id] = cached.get("output") or {}
                continue

            async def _call():
                return await tools.call(tenant_id, tool, {"action_id": action_id, "args": args, "timeout_ms": timeout_ms})

            try:
                out = await run_with_retry(_call, retry_cfg)
                res = {"step_id": step_id, "action_id": action_id, "tool": tool, "status": "succeeded", "output": out, "policy_ids": step.get("policy_ids") or []}
                await self.save_step_result(idem_key, res)
                results.append(res)
                outputs[step_id] = out or {}
            except Exception as e:
                err = classify_error(e)
                res = {"step_id": step_id, "action_id": action_id, "tool": tool, "status": "failed", "error": err, "output": {}, "policy_ids": step.get("policy_ids") or []}
                await self.save_step_result(idem_key, res)
                results.append(res)

        return results

    async def append_audit(self, record: Dict[str, Any]) -> None:
        await self.init()
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO audit_log(record) VALUES($1::jsonb)", json.dumps(record, ensure_ascii=False))

    async def verify_chain(self, run_id: str, limit: int = 500000) -> Dict[str, Any]:
        await self.init()
        async with self.pool.acquire() as con:
            rows = await con.fetch("SELECT seq, canonical, prev_hash, h, kidash FROM run_events WHERE run_id=$1 ORDER BY seq ASC LIMIT $2", run_id, int(limit))
        prev = ""
        bad = []
        for r in rows:
            canonical = dict(r["canonical"])
            canonical_str = _canonical_json(canonical)
            kid = str(r.get("key_id") or _active_kid())
            expected = _hash(prev, canonical_str, kid)
            if (r["prev_hash"] or "") != prev or (r["hash"] or "") != expected:
                bad.append({"seq": int(r["seq"]), "expected_prev": prev, "stored_prev": r["prev_hash"], "expected_hash": expected, "stored_hash": r["hash"], "kid": kid})
            prev = r["hash"] or ""
        return {"ok": len(bad) == 0, "checked": len(rows), "bad": bad[:20]}


async def consume_llm_quota(
    self,
    tenant_ctx: Dict[str, Any],
    tenant_id: str,
    org_id: str | None,
    user_id: str | None,
    model: str,
    tokens: int,
    cost_units: int,
    run_id: str,
    kind: str,
    meta: Dict[str, Any] | None = None,
) -> None:
    """Bank-grade quotas + billing ledger. Atomic daily counters by scope."""
    await self.init()
    day = datetime.date.today()

    quotas = (tenant_ctx or {}).get("llm_quotas") or {}
    # shape:
    # { "per_model": {"gemini-...":{"max_tokens_per_day":..., "max_cost_units_per_day":..., "max_calls_per_day":...}}, "org_per_model": {...}, "user_per_model": {...}}
    def _limits_for(scope_key: str) -> Dict[str, int]:
        scope = quotas.get(scope_key) or {}
        per_model = scope.get("per_model") or {}
        return {k: int(v) for k,v in (per_model.get(model) or {}).items()}

    lim_tenant = _limits_for("tenant")
    lim_org = _limits_for("org")
    lim_user = _limits_for("user")

    async def _hit(scope: str, scope_id: str | None, lim: Dict[str, int]) -> None:
        if not scope_id:
            return
        max_tokens = int(lim.get("max_tokens_per_day", 10**18))
        max_cost = int(lim.get("max_cost_units_per_day", 10**18))
        max_calls = int(lim.get("max_calls_per_day", 10**18))
        async with self.pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT tokens, cost_units, calls FROM llm_usage_daily WHERE scope=$1 AND scope_id=$2 AND day=$3 AND model=$4 FOR UPDATE",
                    scope, scope_id, day, model
                )
                if row:
                    t = int(row["tokens"]) + int(tokens)
                    c = int(row["cost_units"]) + int(cost_units)
                    calls = int(row["calls"]) + 1
                    if t > max_tokens or c > max_cost or calls > max_calls:
                        raise RuntimeError(f"LLM quota exceeded for {scope}:{scope_id} model={model}")
                    await con.execute(
                        "UPDATE llm_usage_daily SET tokens=$1, cost_units=$2, calls=$3, updated_at=now() WHERE scope=$4 AND scope_id=$5 AND day=$6 AND model=$7",
                        t, c, calls, scope, scope_id, day, model
                    )
                else:
                    if tokens > max_tokens or cost_units > max_cost or 1 > max_calls:
                        raise RuntimeError(f"LLM quota exceeded for {scope}:{scope_id} model={model}")
                    await con.execute(
                        "INSERT INTO llm_usage_daily(scope, scope_id, day, model, tokens, cost_units, calls) VALUES($1,$2,$3,$4,$5,$6,1)",
                        scope, scope_id, day, model, int(tokens), int(cost_units)
                    )

    # enforce quotas in order (tenant, org, user)
    await _hit("tenant", tenant_id, lim_tenant)
    await _hit("org", org_id, lim_org)
    await _hit("user", user_id, lim_user)

    # append ledger (best effort)
    meta2 = meta or {}
    async with self.pool.acquire() as con:
        await con.execute(
            "INSERT INTO billing_ledger(tenant_id, org_id, user_id, run_id, kind, model, tokens, cost_units, meta) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)",
            tenant_id, org_id, user_id, run_id, kind, model, int(tokens), int(cost_units), json.dumps(meta2, ensure_ascii=False)
        )

async def billing_daily(self, tenant_id: str, org_id: str | None = None, day: str | None = None) -> List[Dict[str, Any]]:
    await self.init()
    if day:
        d = datetime.date.fromisoformat(day)
    else:
        d = datetime.date.today()
    async with self.pool.acquire() as con:
        if org_id:
            rows = await con.fetch(
                "SELECT scope, scope_id, model, tokens, cost_units, calls FROM llm_usage_daily WHERE day=$1 AND scope='org' AND scope_id=$2",
                d, org_id
            )
        else:
            rows = await con.fetch(
                "SELECT scope, scope_id, model, tokens, cost_units, calls FROM llm_usage_daily WHERE day=$1 AND scope='tenant' AND scope_id=$2",
                d, tenant_id
            )
    return [dict(r) for r in rows]


async def seed_audit_keys(self) -> None:
    """Best effort: seed audit keys from env keyring into DB."""
    await self.init()
    try:
        async with self.pool.acquire() as con:
            for k in _KEYRING.get("keys", []):
                await con.execute(
                    "INSERT INTO audit_keys(kid, secret, active) VALUES($1,$2,$3) "
                    "ON CONFLICT (kid) DO UPDATE SET secret=EXCLUDED.secret, active=audit_keys.active",
                    str(k["kid"]), str(k["secret"]), bool(k.get("active", False))
                )
            # ensure one active in db matches active_kid
            await con.execute("UPDATE audit_keys SET active = (kid=$1)", _active_kid())
    except Exception:
        pass

async def list_audit_keys(self) -> List[Dict[str, Any]]:
    await self.init()
    async with self.pool.acquire() as con:
        rows = await con.fetch("SELECT kid, active, created_at FROM audit_keys ORDER BY created_at DESC")
    return [dict(r) for r in rows]

async def rotate_audit_key(self, kid: str, secret: str, make_active: bool = True) -> None:
    """Store new key and (optionally) activate it. Note: secrets are stored in plaintext in DB (template)."""
    await self.init()
    async with self.pool.acquire() as con:
        async with con.transaction():
            await con.execute(
                "INSERT INTO audit_keys(kid, secret, active) VALUES($1,$2,$3) "
                "ON CONFLICT (kid) DO UPDATE SET secret=EXCLUDED.secret, active=EXCLUDED.active",
                kid, secret, bool(make_active)
            )
            if make_active:
                await con.execute("UPDATE audit_keys SET active = (kid=$1)", kid)
