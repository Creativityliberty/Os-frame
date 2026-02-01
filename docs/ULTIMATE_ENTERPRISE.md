# Ultimate Enterprise Layer

This layer extends **Hardcore** with:

1) **Materialized projections** (runs_mv, approvals_mv) for fast queryable UI
2) **Signed event chain** per run (tamper-proof log) + `/runs/{run_id}/verify`
3) **Policy DSL** (deny/allow/approval/cost overrides) with no hardcoded business logic
4) **Rate-limits** per tenant/user/org (Postgres counters) with a single config surface
5) **LLM cost accounting** using Gemini usage metadata when available, otherwise estimates

---

## 1) Materialized Views

Created at startup (Postgres):

- `runs_mv`: fast list view for dashboard (state/title/tags/budget/updated_at)
- `approvals_mv`: fast list view for approvals

Refresh:
- best-effort auto refresh every `REFRESH_MV_EVERY` events (default 50)
- worker also calls refresh after each job
- manual endpoint: `POST /admin/refresh-mv`

Env:
- `REFRESH_MV_EVERY=50`

---

## 2) Signed Event Chain

`run_events` stores:
- `seq` (monotonic per run)
- `canonical` JSON
- `prev_hash` + `hash` (sha256 chain)

Hash:
`hash = sha256(prev_hash + "|" + canonical_json + "|" + AUDIT_SECRET)`

Verify:
`GET /runs/{run_id}/verify`

Env:
- `AUDIT_SECRET` MUST be set in prod.

---

## 3) Policy DSL

Registry `policies` can be written as rules:

```json
{
  "policy_id": "dsl_approval_email_v1",
  "phase": "exec",
  "priority": 90,
  "when": { "action": "send_email", "roles_any": ["support_agent","admin"] },
  "effect": { "require_approval": true, "set_cost_units": 25 }
}
```

Supported `when`:
- `action`: wildcard pattern (e.g. `crm:*`)
- `tool`: wildcard pattern (e.g. `mcp:*`)
- `roles_any`: list
- `roles_all`: list

Supported `effect`:
- `deny`: bool (+ optional `deny_reason`)
- `require_approval`: bool
- `set_cost_units`: int (overrides action cost units)

---

## 4) Rate limiting

Fixed window counters (default 60s window):

Keys:
- `tenant:{tenant_id}`
- `org:{org_id}`
- `user:{user_id}`

Config (tenant file):
```json
"rate_limits": {
  "tenant_rpm": 600,
  "org_rpm": 600,
  "user_rpm": 120
}
```

Env:
- `RATE_LIMIT_WINDOW_S=60`

---

## 5) LLM cost accounting

Gemini planner records `last_usage`:
- uses SDK `usage_metadata` if available
- else `estimated_total_tokens` from char length

Budget uses:
- `llm_calls` increments by 1 per call
- `cost_units` uses `llm_cost_units_per_1k_tokens` (default fallback to llm_call_cost_units)

Tenant limits:
```json
"limits": {
  "max_llm_calls": 30,
  "max_cost_units": 1500,
  "llm_cost_units_per_1k_tokens": 10
}
```

---

## Production checklist

- set `AUDIT_SECRET` (strong secret)
- set `JWT_SECRET`
- set `GEMINI_API_KEY` and `GEMINI_MODEL`
- run behind nginx (`http://localhost:8080`) for same-origin cookies
- scale workers (`--scale worker=N`)
