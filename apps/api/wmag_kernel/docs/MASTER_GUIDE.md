# Aether-on-WMAG — Guide complet (A → Z)

Tu as ici un **système complet et “free/offline by default”** :
- un **cockpit web** (Aether UI) qui pilote une mission et affiche DAG + artifacts
- un **backend FastAPI** qui exécute un **WMAG Kernel** (PocketFlow) et stream en SSE
- un **kernel fiable** (event log, idempotency, policy gate, approvals)

> “Free” = tu peux run **sans clés LLM** : le planner/toolrunner sont en **stub** par défaut.  
> Tu peux plug un vrai LLM plus tard (OpenAI/Gemini/local) sans changer les contrats.

---

## 1) Structure du repo

```
aether-on-wmag/
  apps/
    api/               # FastAPI + WMAG kernel vendored
    web/               # cockpit React/Vite
  docs/                # spec + runbook + contracts
  docker-compose.yml
  README.md
```

---

## 2) Architecture (mental model)

```
[Aether UI] --HTTP/SSE--> [API FastAPI] ----> [WMAG Kernel (PocketFlow)]
                                   |              |
                                   |              +--> Registry (actions/tools/policies)
                                   |              +--> Storage (event log + step cache)
                                   |              +--> ToolRunner (HTTP/MCP/internal)
                                   |
                                   +--> SSE stream (persist-before-send)
```

- LLM ne “fait pas la prod”: il **propose** un plan JSON
- Le runtime **valide + gate + exécute** de façon déterministe

---

## 3) Run local (sans aucune clé)

### Backend
```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8787
```

### Frontend
```bash
cd apps/web
npm i
npm run dev
```

Ouvre `http://localhost:5173`

---

## 4) API (contrat minimal)

- `POST /missions` → crée run
- `GET /runs/{run_id}/subscribe` → SSE (replay + live)
- `POST /runs/{run_id}/approve` → decision approval

---

## 5) Fiabilité (rails)

- **Persist-before-send** : tout event streamé est persisté avant d’être envoyé
- **Idempotency** : les side-effects ont une `idempotency_key`
- **Retry taxonomy** : retry piloté par le runtime, pas par le LLM
- **Event log** : append-only, support replay/audit

---

## 6) Où lire la spec

Lis dans cet ordre :

1) `docs/design.md`
2) `docs/contracts.md`
3) `docs/streaming_contract.md`
4) `docs/action_registry.md`
5) `docs/runbook.md`
6) `docs/pocketflow_nodes.md`
7) `docs/a2a_state_machine.md`

---

## 7) Plugger un vrai LLM (plus tard)

Remplace `StubPlanner` par un planner réel (OpenAI/Gemini/local) qui sort un plan JSON validable.
Le point clé : **le plan doit respecter `plan.schema.json`**.

---

## 8) Plugger MCP (plus tard)

Remplace `StubToolRunner` par un `MCPToolRunner` :
- discover tools → ToolContracts
- registry expose ActionSpecs
- runtime exécute via ToolRunner

---

## 9) Checklist “prod”

- Storage Postgres (events + step_cache + approvals)
- SSE reconnect cursor (since_seq)
- rate limits + circuit breakers
- multi-tenant registry overrides
- auth (JWT) + RBAC
