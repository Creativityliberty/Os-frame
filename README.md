# Aether Template — Front + Back + WMAG Kernel (PocketFlow)

Monorepo prêt à cloner pour construire **ton orchestrateur** :
- **Front**: Cockpit (React/Vite) — créer mission, suivre DAG/events, approvals
- **Back**: FastAPI — endpoints + SSE (replay + tail) + approvals
- **System**: WMAG Kernel (PocketFlow) — planning → gate → execute → persist → stream

## Quickstart (dev)

### 1) API
```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../../.env.example .env
uvicorn app.main:app --reload --port 8787
```

### 2) Web
```bash
cd apps/web
npm i
npm run dev
```

Open:
- Web: http://localhost:5173
- API: http://localhost:8787/healthz

## Prod (docker)
```bash
docker compose up --build
```

Docs:
- `docs/MASTER.md` (A→Z)
- `docs/PROD.md` (Postgres + SSE reconnect + MCP)


## Enterprise
See `docs/ENTERPRISE.md`.

## Enterprise+
See `docs/ENTERPRISE_PLUS.md`.

## Hardcore Prod
See `docs/PRO_HARDCORE.md`.

## Ultimate Enterprise
See `docs/ULTIMATE_ENTERPRISE.md`.

## Bank-Grade
See `docs/BANK_GRADE.md`.

## Bank-Grade++
See `docs/BANK_GRADE_PLUSPLUS.md`.
