# Enterprise Layer (Auth + RBAC + Budgets + Tenant overrides + Projections)

## Auth
- Simple API Key auth.
- Header: `X-API-Key: <key>`
- SSE/EventSource: `?api_key=<key>` (because headers aren't supported)

Config:
- `apps/api/config/users.json`

Example:
- `dev_admin_key` → admin
- `dev_agent_key` → support_agent

## RBAC
- API: admin endpoints require role `admin`.
- Kernel: policy gate enforces:
  - tenant `roles`
  - action `security.allowed_roles`
  - action `security.requires_approval`
  - optional tenant allowlists (allowed_tools/actions)

Where:
- `kernel/runtime/policy.py` (policy_gate_plan)

## Budgets
Tenant limits:
- `limits.max_tool_calls`
- `limits.per_tool` (optional tool-level caps)

Enforced at execution time in storage adapters:
- `kernel/adapters/storage_inmemory.py`
- `kernel/adapters/storage_postgres.py`

## Multi-tenant registry overrides
Base registry is loaded from `REGISTRY_PATH`.

Optional file override:
- `TENANT_OVERRIDES_DIR=./config/tenants`
- `<dir>/<tenant_id>/registry_override.json`

This is merged into `registry.tenant_overrides[tenant_id]`.

## Projections
- `GET /runs` → list tenant runs
- `GET /approvals` → pending approvals queue
- `GET /runs/{run_id}/events` → run + event batch

Used by the Cockpit "RUNS" tab.
