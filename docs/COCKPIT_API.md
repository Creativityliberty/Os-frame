# Cockpit API (Back)

## Registry
- `GET /registry` → retourne le registry JSON (tools/actions/retry/policies)
- `PUT /registry` → sauvegarde le registry JSON

Le chemin fichier est `REGISTRY_PATH`.

## MCP servers
- `GET /mcp/servers`
- `POST /mcp/servers` body `{id, base_url, timeout_s}`
- `DELETE /mcp/servers/{id}`
- `POST /mcp/discover` → appelle `GET {base_url}/tools` sur chaque server

Le backend stocke la config dans `apps/api/config/mcp_servers.json`.

## Runs
- `GET /runs/{run_id}/subscribe?since_seq=` → SSE replay + tail
- `GET /runs/{run_id}/events?since_seq=` → récupérer events (read model) en JSON
