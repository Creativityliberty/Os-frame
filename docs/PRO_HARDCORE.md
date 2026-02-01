# Hardcore Production Mode

This layer adds:

1) Reverse proxy nginx (same-origin)
2) Worker pool + jobs queue (multi-process)
3) Tenant concurrency caps using Postgres advisory locks
4) Immutable event store + snapshots + audit log (best-effort)
5) Policy engine: approvals/budgets/allow/deny registry-driven

## 1) Reverse proxy (same-origin)

`nginx` listens on `http://localhost:8080`.

- Web: `/` → `web:5173`
- API: `/api/*` → `api:8787`

This enables cookie auth without cross-origin complexity.

## 2) Worker pool

The API enqueues jobs in Postgres `jobs`.
The `worker` service claims jobs with `FOR UPDATE SKIP LOCKED`.

Scale workers:
```bash
docker compose up --build --scale worker=3
```

## 3) Concurrency per tenant

Workers attempt to acquire one of N advisory locks per tenant:
- `TENANT_MAX_CONCURRENCY` slots (default 2)
If none available, job is re-queued.

## 4) Event store + snapshots

- `run_events` is append-only (insert-only).
- `run_snapshots` stores last_seq/state/title/tags/budget for faster UI listing.
- `audit_log` records failures and selected admin actions.

Env:
- `SNAPSHOT_EVERY=25`

## 5) Policy engine (registry-driven)

Registry controls:
- roles → capabilities (API authorization)
- policies (deny/allow tools/actions, require approvals)
- limits (budget overrides)

Files:
- `apps/api/wmag_kernel/kernel/runtime/policy_engine.py`
- `apps/api/wmag_kernel/kernel/runtime/policy.py`

Example policy:
```json
{
  "policy_id": "default_guardrails",
  "phase": "exec",
  "deny_actions": ["dangerous:*"],
  "require_approval_actions": ["send_email", "write_crm:*"]
}
```
