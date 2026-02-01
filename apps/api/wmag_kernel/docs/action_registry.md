# Spec — Action Registry (versioning, taxonomy, retry)

## Why
LLM orchestration becomes reliable when **tools are treated as stable contracts**:
- versioned ids
- strict schemas
- explicit retry strategy
- clear error taxonomy
- deterministic idempotency

## Action record schema

```json
{
  "action_id": "act_ticket_create_v1",
  "version": 1,
  "description": "Create support ticket",
  "schema_in": { "type": "object", "properties": { "title": {"type":"string"} }, "required":["title"] },
  "schema_out": { "type": "object", "properties": { "ticket_id": {"type":"string"} }, "required":["ticket_id"] },
  "side_effect": true,
  "retry_class": "transient_network",
  "idempotency": {
    "strategy": "hash",
    "fields": ["title","customer_id"]
  }
}
```

## Error taxonomy (minimal)

- `transient_network`: timeouts, 502/503, DNS
- `rate_limited`: 429
- `auth`: 401/403
- `invalid_input`: schema validation
- `not_found`: missing resource
- `conflict`: optimistic lock / duplicate
- `policy_denied`: not allowed by tenant policy
- `internal`: unexpected

## Retry classes

| class | retries | backoff | notes |
|---|---:|---:|---|
| transient_network | 3-5 | exp | safe if idempotent |
| rate_limited | 2-4 | exp+reset | respect retry-after |
| auth | 0 | - | fail fast |
| invalid_input | 0 | - | fail fast |
| not_found | 0-1 | small | often permanent |
| conflict | 1-3 | exp | may succeed after refresh |
| internal | 0-1 | small | alerts |

## Determinism rule
Any step must compute:
- `idem_key = hash(action_id + normalized_args + tenant_id)`
Then:
- if exists in cache -> return cached result
- else execute and persist result

This is what makes replay possible and avoids double side-effects.

