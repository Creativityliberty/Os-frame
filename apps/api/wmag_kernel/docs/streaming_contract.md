# Streaming contract (SSE)

Events are emitted as SSE `data: <json>` payloads.

## 1) Status event

```json
{
  "type": "TaskStatusUpdateEvent",
  "ts": "2026-01-31T00:00:00Z",
  "task_id": "…",
  "run_id": "…",
  "state": "submitted|working|input-required|completed|failed|canceled",
  "message": "human readable",
  "meta": { "optional": "…" }
}
```

## 2) Artifact event

```json
{
  "type": "TaskArtifactUpdateEvent",
  "ts": "…",
  "task_id": "…",
  "run_id": "…",
  "artifact_type": "plan|context_pack|step_result|final",
  "artifact": { "any": "json" }
}
```

## Reconnect rule
Server should:
1. replay persisted events for `run_id`
2. continue streaming live execution

In this repo, replay is supported by in-memory store for demo.
Replace with Postgres for production.

