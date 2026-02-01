# Enterprise+ Layer

This template adds production-grade building blocks:

- JWT sessions (access/refresh) + server-side session store
- RBAC driven by registry `roles.capabilities`
- Budgets: tool_calls, llm_calls, cost_units + per_tool/per_action caps
- Run metadata: title/tags + search + export
- Layered registry overrides: base → org → tenant → user

## 1) Auth (JWT sessions)

### Login
`POST /auth/login` with `{ "api_key": "..." }`

Returns:
- `access_token` (JWT, short-lived)
- `refresh_token` (opaque, long-lived; stored server-side hashed)

### Refresh
`POST /auth/refresh` with `{ "refresh_token": "..." }` → new `access_token`

### SSE
Because browsers can't set headers for EventSource, use query param:
`/runs/{run_id}/subscribe?access_token=<JWT>`

Env:
- `JWT_SECRET` (change in prod)
- `JWT_ACCESS_TTL_S`, `JWT_REFRESH_TTL_S`

Session storage:
- In-memory by default
- Postgres if `DATABASE_URL` is set (table `sessions` auto-created)

## 2) RBAC (registry-driven)

Registry contains a `roles` section mapping roles → capabilities:

Example:
```json
"roles": {
  "admin": ["*"],
  "support_agent": ["runs:read","runs:write","approvals:write"],
  "viewer": ["runs:read"]
}
```

API endpoints require capabilities:
- `registry:read`, `registry:write`
- `mcp:manage`
- `runs:read`, `runs:write`
- `approvals:write`

## 3) Budgets

Tenant config file:
`apps/api/config/tenants/<tenant_id>.json`

Example:
```json
"limits": {
  "max_tool_calls": 50,
  "max_llm_calls": 20,
  "max_cost_units": 500,
  "llm_call_cost_units": 10,
  "per_tool": { "weather": 10 },
  "per_action": { "send_email": 50 }
}
```

Costs:
- Each tool step consumes:
  - `tool_calls += 1`
  - `cost_units += action.cost_units (default 1)`
- LLM calls consume:
  - `llm_calls += 1`
  - `cost_units += llm_call_cost_units`

Enforced in storage adapters (`consume_budget`) before execution.

## 4) Run metadata + search + export

- `PATCH /runs/{run_id}` to set `title`, `tags`
- `GET /runs?query=&state=&tag=` search/filter
- `GET /runs/{run_id}/export` returns JSON bundle `{run, events}`

## 5) Layered registry overrides

Effective registry is:
`base REGISTRY_PATH` + (optional) overrides from `REGISTRY_LAYERS_DIR`:

- `config/orgs/<org_id>/registry_override.json`
- `config/tenants/<tenant_id>/registry_override.json`
- `config/users/<user_id>/registry_override.json`

Merge behavior:
- `tools/actions/policies` merged by id key (`tool_id`, `action_id`, `policy_id`)
- other fields deep-merged

Endpoint:
- `GET /registry/effective` shows your merged registry.

