# Prod mode

## Postgres
Activé via `USE_POSTGRES=1` (ou `DATABASE_URL`).

## SSE reconnect
Le backend ajoute `_seq` à chaque event.
Reconnect:
`/runs/{run_id}/subscribe?since_seq=<last_seq>`

## MCP (optionnel)
Définir `MCP_SERVERS` puis référencer les tools comme:
`mcp:<server_id>/<tool_name>` dans le registry.
