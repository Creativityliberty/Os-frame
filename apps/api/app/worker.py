from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wmag_kernel"))

from kernel.flow import Kernel
from kernel.adapters.storage_postgres import PostgresStorage
from kernel.adapters.registry_fs import FSRegistryProvider
from kernel.adapters.index_inmemory import InMemoryIndexProvider
from kernel.adapters.planner_llm_stub import StubPlanner
from kernel.adapters.planner_gemini import GeminiPlanner
from kernel.adapters.hydrator_stub import StubHydrator
from kernel.adapters.toolrunner_stub import StubToolRunner
from kernel.adapters.toolrunner_mcp_http import MCPHttpToolRunner


DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://postgres:postgres@db:5432/aether"
POLL_MS = int(os.getenv("WORKER_POLL_MS", "300"))
TENANT_MAX_CONCURRENCY = int(os.getenv("TENANT_MAX_CONCURRENCY", "2"))

STORAGE = PostgresStorage(DATABASE_URL)
REGISTRY = FSRegistryProvider.from_env()
INDEX = InMemoryIndexProvider()

if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
    PLANNER = GeminiPlanner()
else:
    PLANNER = StubPlanner()

HYDRATOR = StubHydrator()
if os.getenv("MCP_SERVERS"):
    TOOLS = MCPHttpToolRunner.from_env()
else:
    TOOLS = StubToolRunner()

KERNEL = Kernel(storage=STORAGE, registry=REGISTRY, index=INDEX, planner=PLANNER, hydrator=HYDRATOR, tools=TOOLS)

def _tenant_lock_key(tenant_id: str) -> int:
    # stable 64-bit signed int from tenant_id
    import hashlib
    h = hashlib.sha256(tenant_id.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big", signed=False) % (2**63 - 1)

async def _ensure_jobs_table():
    await STORAGE.init()
    async with STORAGE.pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
          job_id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          task_payload JSONB NOT NULL,
          tenant_id TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued',
          locked_at TIMESTAMPTZ,
          locked_by TEXT,
          created_at TIMESTAMPTZ DEFAULT NOW(),
          updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, updated_at);
        """)

async def enqueue_job(run_id: str, tenant_id: str, task_payload: Dict[str, Any]) -> str:
    await _ensure_jobs_table()
    import secrets
    job_id = secrets.token_hex(12)
    async with STORAGE.pool.acquire() as con:
        await con.execute(
            "INSERT INTO jobs(job_id, run_id, tenant_id, task_payload, status) VALUES($1,$2,$3,$4,'queued')",
            job_id, run_id, tenant_id, json.dumps(task_payload)
        )
    return job_id

async def claim_job(worker_id: str) -> Optional[Dict[str, Any]]:
    await _ensure_jobs_table()
    async with STORAGE.pool.acquire() as con:
        row = await con.fetchrow("""
        WITH cte AS (
          SELECT job_id FROM jobs
          WHERE status='queued'
          ORDER BY updated_at ASC
          FOR UPDATE SKIP LOCKED
          LIMIT 1
        )
        UPDATE jobs j SET status='running', locked_at=NOW(), locked_by=$1, updated_at=NOW()
        FROM cte WHERE j.job_id=cte.job_id
        RETURNING j.job_id, j.run_id, j.tenant_id, j.task_payload;
        """, worker_id)
    if not row:
        return None
    return dict(row)

async def complete_job(job_id: str, ok: bool, err: Optional[str] = None) -> None:
    async with STORAGE.pool.acquire() as con:
        await con.execute(
            "UPDATE jobs SET status=$1, updated_at=NOW() WHERE job_id=$2",
            ("done" if ok else "failed"), job_id
        )
    if err:
        # best effort audit
        await STORAGE.append_audit({"type":"worker_job_failed","job_id":job_id,"error":err})

async def try_lock_tenant(con, tenant_id: str) -> bool:
    key = _tenant_lock_key(tenant_id)
    # advisory lock; shared limit is achieved by using multiple lock keys per tenant
    # to allow N concurrency, we try keys (key+i) for i in 0..N-1
    for i in range(TENANT_MAX_CONCURRENCY):
        r = await con.fetchval("SELECT pg_try_advisory_lock($1)", key + i)
        if r:
            return True
    return False

async def unlock_tenant(con, tenant_id: str) -> None:
    key = _tenant_lock_key(tenant_id)
    # release any lock we might hold; try all slots
    for i in range(TENANT_MAX_CONCURRENCY):
        try:
            await con.execute("SELECT pg_advisory_unlock($1)", key + i)
        except Exception:
            pass

async def run_loop():
    worker_id = os.getenv("WORKER_ID") or f"worker-{os.getpid()}"
    print(f"[worker] starting: {worker_id} poll={POLL_MS}ms tenant_max={TENANT_MAX_CONCURRENCY}")

    while True:
        job = await claim_job(worker_id)
        if not job:
            await asyncio.sleep(POLL_MS / 1000.0)
            continue

        job_id = job["job_id"]
        run_id = job["run_id"]
        tenant_id = job["tenant_id"]
        task_payload = job["task_payload"]
        if isinstance(task_payload, str):
            task_payload = json.loads(task_payload)

        # tenant concurrency lock
        async with STORAGE.pool.acquire() as con:
            got = await try_lock_tenant(con, tenant_id)
            if not got:
                # push back
                await con.execute("UPDATE jobs SET status='queued', locked_at=NULL, locked_by=NULL, updated_at=NOW() WHERE job_id=$1", job_id)
                await asyncio.sleep(POLL_MS / 1000.0)
                continue

        ok = True
        err = None
        try:
            async for _ev in KERNEL.task_send_subscribe(task_payload):
                pass
            try:
                await STORAGE.refresh_materialized_views()
            except Exception:
                pass
        except Exception as e:
            ok = False
            err = str(e)
        finally:
            async with STORAGE.pool.acquire() as con:
                await unlock_tenant(con, tenant_id)

        await complete_job(job_id, ok=ok, err=err)

async def main():
    await _ensure_jobs_table()
    await run_loop()

if __name__ == "__main__":
    asyncio.run(main())
