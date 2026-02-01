# Bank-Grade Layer

This layer adds *strict governance* to Ultimate Enterprise:

1) Materialized Views refresh **CONCURRENTLY** (safe, non-blocking reads)
2) Signed event chain with **key rotation + key_id per event**
3) Policy DSL with **composed conditions** (AND/OR/NOT) + **obligations**
4) LLM quotas per model + org billing ledger

---

## 1) Concurrent MV refresh

Endpoint:
- `POST /admin/refresh-mv?concurrently=1`

Storage:
- `refresh_materialized_views(concurrently: bool)`

Requirements:
- MV must have a UNIQUE index (already created in init).

Note:
- `REFRESH MATERIALIZED VIEW CONCURRENTLY` cannot run inside a transaction.

---

## 2) Signed Event Chain with Key Rotation

Env (preferred):
- `AUDIT_KEYS_JSON='[{"kid":"k1","secret":"...","active":true},{"kid":"k0","secret":"...","active":false}]'`

Fallback:
- `AUDIT_SECRET` (kid="k0")

Each event row stores `key_id`.
Verify:
- `GET /runs/{run_id}/verify`

Rotation process:
1. Add new key with `active:true` + keep old key in list.
2. Deploy.
3. New events use the new kid.
4. Keep old key until all logs that matter are archived.

---

## 3) Policy DSL: composition + obligations

### When composition
```json
{ "all": [ {"action":"send_email"}, {"not":{"roles_any":["admin"]}} ] }
```

Operators:
- `all`: AND
- `any`: OR
- `not`: NOT

### Obligations
Example:
```json
{
  "effect": {
    "obligations": [
      {"type":"must_emit_artifact","artifact_type":"final_reply"}
    ]
  }
}
```
Enforcement:
- After execution, kernel checks that required artifact types exist in the run event stream.

---

## 4) LLM quotas + billing ledger

Tables:
- `llm_usage_daily(scope, scope_id, day, model, tokens, cost_units, calls)`
- `billing_ledger(ts, tenant_id, org_id, user_id, run_id, kind, model, tokens, cost_units, meta)`

Config (tenant context json):
```json
"llm_quotas": {
  "tenant": {"per_model": {"gemini-3-flash-preview": {"max_tokens_per_day": 200000, "max_cost_units_per_day": 5000, "max_calls_per_day": 2000}}},
  "org": {"per_model": {"gemini-3-flash-preview": {"max_tokens_per_day": 100000, "max_cost_units_per_day": 2500, "max_calls_per_day": 1000}}},
  "user": {"per_model": {"gemini-3-flash-preview": {"max_tokens_per_day": 50000, "max_cost_units_per_day": 1000, "max_calls_per_day": 200}}}
}
```

Endpoint:
- `GET /billing/daily?day=YYYY-MM-DD`
- Optional (admin): `GET /billing/daily?org_id=...`

---

## Prod checklist

- set `AUDIT_KEYS_JSON` (recommended) OR `AUDIT_SECRET`
- set `JWT_SECRET`
- set `GEMINI_API_KEY` + `GEMINI_MODEL`
- scale workers
- verify logs with `/runs/{run_id}/verify`
