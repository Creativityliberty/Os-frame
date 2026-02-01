# Bank-Grade++ (Parano Layer)

Adds the final governance pieces:

1) **MV Refresh Scheduler** with exponential backoff (no ops babysitting)
2) **HMAC signed event chain** (better cryptographic construction than sha256 concatenation)
3) **Audit key registry + rotation API** (kid, active flag)
4) **Policy evidence**: each step result carries `policy_ids`
5) **Obligations**:
   - `must_emit_artifact`
   - `must_reference_policy_id` for side effects
6) **Strict idempotency enforcement** for side-effect actions

---

## 1) MV refresh scheduler

Env:
- `MV_REFRESH_INTERVAL_S=60`
- `MV_REFRESH_CONCURRENTLY=1`
- `MV_REFRESH_MAX_BACKOFF_S=600`

Behavior:
- refresh success -> sleep interval
- refresh fail -> backoff doubles until max

---

## 2) HMAC signed chain

Hash:
`hmac_sha256(secret, prev_hash + "|" + canonical_json)`

Row stores:
- `key_id`

---

## 3) Audit key rotation API (template)

Endpoints (admin):
- `GET /admin/audit-keys`
- `POST /admin/audit-keys/rotate`

Body:
```json
{"kid":"k2","secret":"...","make_active":true}
```

Important:
- This template stores secrets in plaintext in DB.
- In real prod: store in KMS or encrypted at rest.

---

## 4) Policy evidence

Policy engine attaches matched `policy_ids` to each step.
Storage includes them in `step_result` artifacts.

---

## 5) Obligations

### must_reference_policy_id
For side-effect steps, require a specific policy id to appear in `policy_ids`.

Example:
```json
{
  "type":"must_reference_policy_id",
  "policy_id":"dsl_guardrails_v1"
}
```

---

## 6) Idempotency enforcement

Side-effect actions must have:
`args.idempotency_key`

If missing -> step fails with error class `IDEMPOTENCY`.
