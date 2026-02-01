from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request

from app.auth_jwt import User, get_current_user


WINDOW_S = int(os.getenv("RATE_LIMIT_WINDOW_S", "60"))

@dataclass
class RateLimits:
    tenant_rpm: int = 600
    user_rpm: int = 120
    org_rpm: int = 600

def _now_window_start() -> int:
    now = int(time.time())
    return now - (now % WINDOW_S)

class RateLimitStore:
    async def init(self) -> None: ...
    async def hit(self, key: str, limit: int) -> Tuple[int, int]: ...

class InMemoryRateLimitStore(RateLimitStore):
    def __init__(self) -> None:
        self.counters: Dict[Tuple[str,int], int] = {}

    async def init(self) -> None:
        return

    async def hit(self, key: str, limit: int) -> Tuple[int, int]:
        ws = _now_window_start()
        k = (key, ws)
        n = self.counters.get(k, 0) + 1
        self.counters[k] = n
        remaining = max(0, limit - n)
        reset_in = (ws + WINDOW_S) - int(time.time())
        if n > limit:
            raise HTTPException(status_code=429, detail=f"rate limit exceeded for {key}")
        return remaining, max(0, reset_in)

class PostgresRateLimitStore(RateLimitStore):
    def __init__(self, storage):
        self.storage = storage  # PostgresStorage

    async def init(self) -> None:
        await self.storage.init()
        async with self.storage.pool.acquire() as con:
            await con.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
              key TEXT NOT NULL,
              window_start BIGINT NOT NULL,
              count BIGINT NOT NULL DEFAULT 0,
              updated_at TIMESTAMPTZ DEFAULT NOW(),
              PRIMARY KEY (key, window_start)
            );
            """)

    async def hit(self, key: str, limit: int) -> Tuple[int, int]:
        await self.init()
        ws = _now_window_start()
        async with self.storage.pool.acquire() as con:
            # Upsert + increment atomically
            row = await con.fetchrow("""
            INSERT INTO rate_limits(key, window_start, count)
            VALUES($1, $2, 1)
            ON CONFLICT (key, window_start)
            DO UPDATE SET count = rate_limits.count + 1, updated_at=NOW()
            RETURNING count;
            """, key, ws)
        n = int(row["count"])
        remaining = max(0, limit - n)
        reset_in = (ws + WINDOW_S) - int(time.time())
        if n > limit:
            raise HTTPException(status_code=429, detail=f"rate limit exceeded for {key}")
        return remaining, max(0, reset_in)

def build_rate_limit_store(storage=None) -> RateLimitStore:
    if storage and getattr(storage, "pool", None) is not None:
        return PostgresRateLimitStore(storage)
    return InMemoryRateLimitStore()

def _load_limits(user: User, tenant_ctx: Dict[str, Any]) -> RateLimits:
    lim = (tenant_ctx or {}).get("rate_limits") or {}
    return RateLimits(
        tenant_rpm=int(lim.get("tenant_rpm", 600)),
        user_rpm=int(lim.get("user_rpm", 120)),
        org_rpm=int(lim.get("org_rpm", 600)),
    )

async def enforce_rate_limits(request: Request, user: User, tenant_ctx: Dict[str, Any], store: RateLimitStore) -> None:
    limits = _load_limits(user, tenant_ctx)
    # keys
    await store.hit(f"tenant:{user.tenant_id}", limits.tenant_rpm)
    await store.hit(f"user:{user.user_id}", limits.user_rpm)
    if user.org_id:
        await store.hit(f"org:{user.org_id}", limits.org_rpm)
