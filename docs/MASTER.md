# Aether Template — MASTER (tout)

Ce template te donne un squelette complet :

## Architecture
Front (Cockpit) -> API (FastAPI) -> WMAG Kernel (PocketFlow) -> Tools (MCP/HTTP)

## Endpoints
- `POST /missions` : create mission (task_id, run_id)
- `GET /runs/{run_id}/subscribe?since_seq=` : SSE replay + tail
- `POST /runs/{run_id}/approve` : approval decision
- `GET /healthz`

## Concepts (WMAG)
- Registry (tools/actions/retry/policies)
- ContextPack (injection mémoire)
- Plan JSON (validé)
- Gate (policy + approval)
- Execute (idempotency + retry)
- Event log (audit + replay)

Voir `apps/api/wmag_kernel/docs/MASTER_GUIDE.md` pour la doc complète kernel.


## Cockpit Pro
- DAG viewer (from plan.steps + depends_on)
- Artifacts panel (plan + step_results + gate)
- Registry editor (GET/PUT /registry)
- MCP servers manager + tool discovery (GET/POST/DELETE /mcp/servers, POST /mcp/discover)
