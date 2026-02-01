# API

Backend FastAPI qui wrap le WMAG Kernel.

## Env
- `.env` ou variables environnements:
  - `USE_POSTGRES=1`
  - `DATABASE_URL=postgresql://...`
  - `REGISTRY_PATH=...`
  - `GEMINI_API_KEY` (optionnel)
  - `MCP_SERVERS` (optionnel)

## Endpoints
- POST /missions
- GET /runs/{run_id}/subscribe?since_seq=
- POST /runs/{run_id}/approve


Cockpit endpoints:
- GET /registry
- PUT /registry
- GET /mcp/servers
- POST /mcp/servers
- DELETE /mcp/servers/{id}
- POST /mcp/discover
- GET /runs/{run_id}/events
