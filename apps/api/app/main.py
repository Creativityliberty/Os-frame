from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from app.auth_jwt import (
    User,
    api_key_to_user,
    make_access_token,
    make_refresh_token,
    SESSION_STORE,
    get_current_user,
)
from app.authz import require_capability
from app.worker import enqueue_job
from app.ratelimit import build_rate_limit_store, enforce_rate_limits



# Load .env if present
load_dotenv()

# Vendored kernel path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wmag_kernel"))

from kernel.flow import Kernel
from kernel.adapters.storage_inmemory import InMemoryStorage
from kernel.adapters.storage_postgres import PostgresStorage
from kernel.adapters.registry_fs import FSRegistryProvider
from kernel.adapters.index_inmemory import InMemoryIndexProvider
from kernel.adapters.planner_llm_stub import StubPlanner
from kernel.adapters.planner_gemini import GeminiPlanner
from kernel.adapters.hydrator_stub import StubHydrator
from kernel.adapters.toolrunner_stub import StubToolRunner
from kernel.adapters.toolrunner_mcp_http import MCPHttpToolRunner


# -----------------------------
# Models
# -----------------------------

class LoginIn(BaseModel):
    api_key: str

class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    user: Dict[str, Any]

class RefreshIn(BaseModel):
    refresh_token: str

class LogoutIn(BaseModel):
    refresh_token: str

class MissionCreateIn(BaseModel):
    tenant_id: Optional[str] = None
    user_message: str
    task_id: Optional[str] = None
    tags: Optional[list[str]] = None
    title: Optional[str] = None

class MissionCreateOut(BaseModel):
    task_id: str
    run_id: str

class ApproveIn(BaseModel):
    decision: str = Field(..., pattern="^(approved|denied)$")
    by: str = "human"
    reason: Optional[str] = None

class MCPServerIn(BaseModel):
    id: str
    base_url: str
    timeout_s: float = 15.0

class RunMetaPatchIn(BaseModel):
    title: Optional[str] = None
    tags: Optional[list[str]] = None


# -----------------------------
# App setup
# -----------------------------

app = FastAPI(title="Aether Enterprise+ API", version="0.5.0")
import asyncio

async def _mv_refresh_loop():
    """Background scheduler: periodically refresh materialized views with backoff."""
    interval = int(os.getenv("MV_REFRESH_INTERVAL_S", "60"))
    concurrently = bool(int(os.getenv("MV_REFRESH_CONCURRENTLY", "1")))
    max_backoff = int(os.getenv("MV_REFRESH_MAX_BACKOFF_S", "600"))
    backoff = interval
    while True:
        try:
            if hasattr(STORAGE, "refresh_materialized_views"):
                await STORAGE.refresh_materialized_views(concurrently=concurrently)
            backoff = interval
        except Exception:
            backoff = min(max_backoff, max(interval, backoff * 2))
        await asyncio.sleep(backoff)


# CORS: in same-origin behind nginx, CORS is not required. If you run web separately, set:
# - CORS_ORIGINS=http://localhost:8080,http://localhost:5173
# - CORS_ALLOW_CREDENTIALS=1 (for cookie auth)
cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
allow_credentials = os.getenv("CORS_ALLOW_CREDENTIALS", "0") == "1"

if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Storage selection
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
USE_POSTGRES = os.getenv("USE_POSTGRES", "0") == "1" or bool(DATABASE_URL)

if USE_POSTGRES:
    if not DATABASE_URL:
        DATABASE_URL = "postgresql://postgres:postgres@db:5432/aether"
    STORAGE = PostgresStorage(DATABASE_URL)
else:
    STORAGE = InMemoryStorage()

# Registry (layered overrides)
REGISTRY = FSRegistryProvider.from_env()
INDEX = InMemoryIndexProvider()

# Rate limiting store (Postgres when available)
RATE_STORE = build_rate_limit_store(STORAGE if USE_POSTGRES else None)

# Planner selection (Gemini optional)
if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
    PLANNER = GeminiPlanner()
else:
    PLANNER = StubPlanner()

HYDRATOR = StubHydrator()

# Tools selection (MCP optional)
if os.getenv("MCP_SERVERS"):
    TOOLS = MCPHttpToolRunner.from_env()
else:
    TOOLS = StubToolRunner()

KERNEL = Kernel(storage=STORAGE, registry=REGISTRY, index=INDEX, planner=PLANNER, hydrator=HYDRATOR, tools=TOOLS)

# Background runner tasks (single-process mode)

# Config dir for cockpit
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(Path(__file__).resolve().parents[1] / "config")))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
MCP_SERVERS_FILE = CONFIG_DIR / "mcp_servers.json"

def _load_mcp_servers() -> list[dict]:
    if MCP_SERVERS_FILE.exists():
        try:
            return json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def _save_mcp_servers(servers: list[dict]) -> None:
    MCP_SERVERS_FILE.write_text(json.dumps(servers, indent=2, ensure_ascii=False), encoding="utf-8")

def _registry_path() -> Path:
    rp = os.getenv("REGISTRY_PATH", "./wmag_kernel/registry/registry_support_v1.json")
    return Path(rp)

def _registry_layers_dir() -> Path:
    return Path(os.getenv("REGISTRY_LAYERS_DIR", str(CONFIG_DIR)))

@app.on_event("startup")
async def _startup():
    if hasattr(STORAGE, "init"):
        await STORAGE.init()
    # start MV refresh loop
    asyncio.create_task(_mv_refresh_loop())
    # sessions storage init
    await SESSION_STORE.init()

@app.on_event("shutdown")
async def _shutdown():
    if hasattr(TOOLS, "close"):
        await TOOLS.close()


# -----------------------------
# SSE helpers
# -----------------------------

def sse_pack(event: Dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


async def ensure_run_started(run_id: str, task_payload: Dict[str, Any]) -> None:
    # In production with Postgres, execution is performed by `worker` via jobs queue.
    if USE_POSTGRES:
        await enqueue_job(run_id=run_id, tenant_id=task_payload.get("tenant_id","tenant"), task_payload=task_payload)
        return
    # Dev fallback: in-process background task
    if run_id in RUN_TASKS and not RUN_TASKS[run_id].done():
        return

    async def _run():
        async for _ev in KERNEL.task_send_subscribe(task_payload):
            await asyncio.sleep(0)

    RUN_TASKS[run_id] = asyncio.create_task(_run())


async def tail_events(run_id: str, since_seq: Optional[int], poll_ms: int = 300) -> AsyncIterator[bytes]:
    cursor = since_seq or 0

    batch = await STORAGE.list_updates(run_id, cursor) if hasattr(STORAGE, "list_updates") else []
    for ev in batch:
        cursor = max(cursor, int(ev.get("_seq", cursor)))
        yield sse_pack(ev)

    while True:
        run = await STORAGE.find_run(run_id) if hasattr(STORAGE, "find_run") else None
        state = (run or {}).get("state")
        if state in ("completed", "failed", "canceled"):
            break

        batch = await STORAGE.list_updates(run_id, cursor) if hasattr(STORAGE, "list_updates") else []
        for ev in batch:
            cursor = max(cursor, int(ev.get("_seq", cursor)))
            yield sse_pack(ev)

        await asyncio.sleep(poll_ms / 1000)

    batch = await STORAGE.list_updates(run_id, cursor) if hasattr(STORAGE, "list_updates") else []
    for ev in batch:
        cursor = max(cursor, int(ev.get("_seq", cursor)))
        yield sse_pack(ev)

    yield b"event: done\ndata: {}\n\n"


# -----------------------------
# Health + Auth
# -----------------------------

@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"ok": True, "mode": "postgres" if USE_POSTGRES else "inmemory"}

@app.post("/auth/login", response_model=LoginOut)
async def login(inp: LoginIn) -> LoginOut:
    u = api_key_to_user(inp.api_key)
    if not u:
        raise HTTPException(status_code=401, detail="invalid api_key")
    refresh = make_refresh_token()
    sid = await SESSION_STORE.create_session(u, refresh)
    access = make_access_token(u, sid)
    return LoginOut(access_token=access, refresh_token=refresh, user={"user_id": u.user_id, "tenant_id": u.tenant_id, "roles": u.roles, "org_id": u.org_id})

@app.post("/auth/refresh")
async def refresh(inp: RefreshIn) -> Dict[str, Any]:
    u, sid = await SESSION_STORE.verify_refresh(inp.refresh_token)
    access = make_access_token(u, sid)
    return {"access_token": access}

@app.post("/auth/logout")
async def logout(inp: LogoutIn) -> Dict[str, Any]:
    await SESSION_STORE.revoke_refresh(inp.refresh_token)
    return {"ok": True}

@app.get("/auth/me")
async def me(user: User = Depends(get_current_user)):
    return {"user_id": user.user_id, "tenant_id": user.tenant_id, "roles": user.roles, "org_id": user.org_id}


# -----------------------------
# Missions + Runs (tenant isolation)
# -----------------------------

@app.post("/missions", response_model=MissionCreateOut, dependencies=[Depends(require_capability("runs:write"))])
async def create_mission(request: Request, inp: MissionCreateIn, user: User = Depends(get_current_user)) -> MissionCreateOut:
    tenant_id = inp.tenant_id or user.tenant_id
    # tenant isolation: only admin can target another tenant
    if tenant_id != user.tenant_id and "*" not in user.roles and "admin" not in user.roles:
        tenant_id = user.tenant_id

    task_id = inp.task_id or str(uuid.uuid4())
    run = await STORAGE.create_or_load_run(task_id=task_id, tenant_id=tenant_id)
    run_id = run["run_id"]

    # attach caller context so registry layering can use user/org
    task_payload = {
        "tenant_id": tenant_id,
        "task_id": task_id,
        "user_message": inp.user_message,
        "user_id": user.user_id,
        "org_id": user.org_id,
        "roles": user.roles,
    }

    if hasattr(STORAGE, "save_task_input"):
        await STORAGE.save_task_input(run_id, task_payload)

    # optional metadata
    if hasattr(STORAGE, "update_run_metadata") and (inp.title is not None or inp.tags is not None):
        await STORAGE.update_run_metadata(run_id, title=inp.title, tags=inp.tags)

    await ensure_run_started(run_id, task_payload)
    return MissionCreateOut(task_id=task_id, run_id=run_id)


@app.get("/runs", dependencies=[Depends(require_capability("runs:read"))])
async def list_runs(
    limit: int = 50,
    offset: int = 0,
    query: Optional[str] = None,
    state: Optional[str] = None,
    tag: Optional[str] = None,
    user: User = Depends(get_current_user),
):
    if hasattr(STORAGE, "list_runs"):
        runs = await STORAGE.list_runs(tenant_id=user.tenant_id, limit=limit, offset=offset, query=query, state=state, tag=tag)
    else:
        runs = []
    return {"runs": runs}


@app.patch("/runs/{run_id}", dependencies=[Depends(require_capability("runs:write"))])
async def patch_run_metadata(run_id: str, inp: RunMetaPatchIn, user: User = Depends(get_current_user)):
    run = await STORAGE.find_run(run_id) if hasattr(STORAGE, "find_run") else None
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if "admin" not in user.roles and run.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    if not hasattr(STORAGE, "update_run_metadata"):
        raise HTTPException(status_code=501, detail="storage doesn't support metadata")
    out = await STORAGE.update_run_metadata(run_id, title=inp.title, tags=inp.tags)
    return {"run": out}


@app.get("/runs/{run_id}/events", dependencies=[Depends(require_capability("runs:read"))])
async def get_run_events(run_id: str, since_seq: Optional[int] = None, user: User = Depends(get_current_user)) -> Dict[str, Any]:
    run = await STORAGE.find_run(run_id) if hasattr(STORAGE, "find_run") else None
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if "admin" not in user.roles and run.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    evs = await STORAGE.list_updates(run_id, since_seq or 0) if hasattr(STORAGE, "list_updates") else []
    return {"run": run, "events": evs}


@app.get("/runs/{run_id}/subscribe", dependencies=[Depends(require_capability("runs:read"))])
async def subscribe(run_id: str, since_seq: Optional[int] = Query(default=None), user: User = Depends(get_current_user)):
    run = await STORAGE.find_run(run_id) if hasattr(STORAGE, "find_run") else None
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if "admin" not in user.roles and run.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")

    task_payload = (run.get("task_input") or {})
    if task_payload:
        await ensure_run_started(run_id, task_payload)

    return StreamingResponse(
        tail_events(run_id, since_seq=since_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/runs/{run_id}/approve", dependencies=[Depends(require_capability("approvals:write"))])
async def approve(run_id: str, inp: ApproveIn, user: User = Depends(get_current_user)):
    run = await STORAGE.find_run(run_id) if hasattr(STORAGE, "find_run") else None
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if "admin" not in user.roles and run.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")

    decision = {"decision": inp.decision, "by": inp.by, "ts": "now", "reason": inp.reason}
    if hasattr(STORAGE, "set_approval_decision"):
        await STORAGE.set_approval_decision(run_id, decision)
    return {"ok": True}


@app.get("/approvals", dependencies=[Depends(require_capability("runs:read"))])
async def list_approvals(status: str = "pending", user: User = Depends(get_current_user)):
    if hasattr(STORAGE, "list_approvals"):
        aps = await STORAGE.list_approvals(tenant_id=user.tenant_id, status=status, limit=100)
    else:
        aps = []
    return {"approvals": aps}


@app.get("/runs/{run_id}/export", dependencies=[Depends(require_capability("runs:read"))])
async def export_run(run_id: str, user: User = Depends(get_current_user)):
    run = await STORAGE.find_run(run_id) if hasattr(STORAGE, "find_run") else None
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if "admin" not in user.roles and run.get("tenant_id") != user.tenant_id:
        raise HTTPException(status_code=403, detail="tenant mismatch")
    events = await STORAGE.list_updates(run_id, 0) if hasattr(STORAGE, "list_updates") else []
    bundle = {"run": run, "events": events}
    return JSONResponse(content=bundle)


# -----------------------------
# Cockpit Admin (capabilities)
# -----------------------------

@app.get("/registry", dependencies=[Depends(require_capability("registry:read"))])
async def get_registry(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    p = _registry_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"registry not found at {p}")
    return json.loads(p.read_text(encoding="utf-8"))

@app.get("/registry/effective", dependencies=[Depends(require_capability("registry:read"))])
async def get_registry_effective(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    task = {"tenant_id": user.tenant_id, "user_id": user.user_id, "org_id": user.org_id}
    return await REGISTRY.load_registry_for(task)

@app.put("/registry", dependencies=[Depends(require_capability("registry:write"))])
async def put_registry(payload: Dict[str, Any], user: User = Depends(get_current_user)) -> Dict[str, Any]:
    if not isinstance(payload, dict) or "actions" not in payload or "tools" not in payload:
        raise HTTPException(status_code=400, detail="invalid registry payload")
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}

@app.get("/mcp/servers", dependencies=[Depends(require_capability("mcp:manage"))])
async def list_mcp_servers(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return {"servers": _load_mcp_servers()}

@app.post("/mcp/servers", dependencies=[Depends(require_capability("mcp:manage"))])
async def add_mcp_server(inp: MCPServerIn, user: User = Depends(get_current_user)):
    servers = _load_mcp_servers()
    servers = [s for s in servers if s.get("id") != inp.id]
    servers.append(inp.model_dump())
    _save_mcp_servers(servers)
    return {"ok": True, "server": inp.model_dump()}

@app.delete("/mcp/servers/{server_id}", dependencies=[Depends(require_capability("mcp:manage"))])
async def delete_mcp_server(server_id: str, user: User = Depends(get_current_user)):
    servers = _load_mcp_servers()
    servers = [s for s in servers if s.get("id") != server_id]
    _save_mcp_servers(servers)
    return {"ok": True}

@app.post("/mcp/discover", dependencies=[Depends(require_capability("mcp:manage"))])
async def discover_mcp_tools(user: User = Depends(get_current_user)):
    servers = _load_mcp_servers()
    out = []
    async with httpx.AsyncClient() as client:
        for s in servers:
            base = str(s.get("base_url","")).rstrip("/")
            try:
                r = await client.get(base + "/tools", timeout=float(s.get("timeout_s", 15.0)))
                r.raise_for_status()
                tools = r.json()
                out.append({"id": s.get("id"), "ok": True, "tools": tools})
            except Exception as e:
                out.append({"id": s.get("id"), "ok": False, "error": str(e), "tools": []})
    return {"results": out}


@app.get("/runs/{run_id}/verify", dependencies=[Depends(require_capability("runs:read"))])
async def verify_run_chain(run_id: str, user: User = Depends(get_current_user)):
    run = await STORAGE.find_run(run_id) if hasattr(STORAGE, "find_run") else None
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.get("tenant_id") != user.tenant_id and "admin" not in user.roles and "*" not in user.roles:
        raise HTTPException(status_code=403, detail="forbidden")
    if not hasattr(STORAGE, "verify_chain"):
        return {"ok": True, "checked": 0, "note": "storage has no verify_chain"}
    return await STORAGE.verify_chain(run_id)

@app.post("/admin/refresh-mv", dependencies=[Depends(require_capability("registry:write"))])
async def admin_refresh_materialized_views(concurrently: int = 0, user: User = Depends(get_current_user)):
    if not hasattr(STORAGE, "refresh_materialized_views"):
        return {"ok": False, "note": "storage has no materialized views"}
    await STORAGE.refresh_materialized_views(concurrently=bool(concurrently))
    return {"ok": True, "concurrently": bool(concurrently)}



@app.get("/billing/daily", dependencies=[Depends(require_capability("runs:read"))])
async def billing_daily(day: str | None = None, org_id: str | None = None, user: User = Depends(get_current_user)):
    if not hasattr(STORAGE, "billing_daily"):
        return []
    # only allow org_id if user is admin
    if org_id and ("admin" not in user.roles and "*" not in user.roles):
        raise HTTPException(status_code=403, detail="forbidden")
    return await STORAGE.billing_daily(tenant_id=user.tenant_id, org_id=org_id, day=day)



@app.get("/admin/audit-keys", dependencies=[Depends(require_capability("registry:write"))])
async def list_audit_keys(user: User = Depends(get_current_user)):
    if not hasattr(STORAGE, "list_audit_keys"):
        return []
    return await STORAGE.list_audit_keys()

class AuditKeyRotateIn(BaseModel):
    kid: str
    secret: str
    make_active: bool = True

@app.post("/admin/audit-keys/rotate", dependencies=[Depends(require_capability("registry:write"))])
async def rotate_audit_key(inp: AuditKeyRotateIn, user: User = Depends(get_current_user)):
    if not hasattr(STORAGE, "rotate_audit_key"):
        raise HTTPException(status_code=400, detail="storage does not support audit keys")
    await STORAGE.rotate_audit_key(inp.kid, inp.secret, make_active=inp.make_active)
    return {"ok": True}

