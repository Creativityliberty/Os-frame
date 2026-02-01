from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, Header, Query

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(Path(__file__).resolve().parents[1] / "config")))
USERS_FILE = CONFIG_DIR / "users.json"

JWT_SECRET = os.getenv("JWT_SECRET", "dev_jwt_secret_change_me")
JWT_ALG = "HS256"
ACCESS_TTL_S = int(os.getenv("JWT_ACCESS_TTL_S", "900"))  # 15m
REFRESH_TTL_S = int(os.getenv("JWT_REFRESH_TTL_S", "1209600"))  # 14d
HASH_SALT = os.getenv("JWT_HASH_SALT", "dev_hash_salt")

@dataclass
class User:
    user_id: str
    tenant_id: str
    roles: List[str]
    org_id: Optional[str] = None

def load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {"keys": []}

def api_key_to_user(api_key: str) -> Optional[User]:
    cfg = load_users()
    for rec in cfg.get("keys", []):
        if rec.get("api_key") == api_key:
            return User(
                user_id=rec.get("user_id","user"),
                tenant_id=rec.get("tenant_id","tenant_demo"),
                roles=list(rec.get("roles") or []),
                org_id=rec.get("org_id"),
            )
    return None

def _sha256(x: str) -> str:
    return hashlib.sha256((HASH_SALT + x).encode("utf-8")).hexdigest()

def make_access_token(user: User, session_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user.user_id,
        "tenant_id": user.tenant_id,
        "roles": user.roles,
        "org_id": user.org_id,
        "sid": session_id,
        "iat": now,
        "exp": now + ACCESS_TTL_S,
        "typ": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def make_refresh_token() -> str:
    # opaque token; stored server-side hashed
    return secrets.token_urlsafe(48)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")

# -----------------------------
# Session store (in-memory by default; postgres if DATABASE_URL exists)
# -----------------------------

class SessionStore:
    async def init(self) -> None: ...
    async def create_session(self, user: User, refresh_token: str) -> str: ...
    async def verify_refresh(self, refresh_token: str) -> Tuple[User, str]: ...
    async def revoke_refresh(self, refresh_token: str) -> None: ...

class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self.sessions: Dict[str, Dict[str, Any]] = {}  # refresh_hash -> record

    async def init(self) -> None:
        return

    async def create_session(self, user: User, refresh_token: str) -> str:
        sid = secrets.token_hex(16)
        rh = _sha256(refresh_token)
        self.sessions[rh] = {
            "sid": sid,
            "user": user,
            "expires_at": int(time.time()) + REFRESH_TTL_S,
            "revoked": False,
        }
        return sid

    async def verify_refresh(self, refresh_token: str) -> Tuple[User, str]:
        rh = _sha256(refresh_token)
        rec = self.sessions.get(rh)
        if not rec or rec.get("revoked"):
            raise HTTPException(status_code=401, detail="invalid refresh")
        if int(rec.get("expires_at", 0)) < int(time.time()):
            raise HTTPException(status_code=401, detail="refresh expired")
        return rec["user"], rec["sid"]

    async def revoke_refresh(self, refresh_token: str) -> None:
        rh = _sha256(refresh_token)
        if rh in self.sessions:
            self.sessions[rh]["revoked"] = True

class PostgresSessionStore(SessionStore):
    def __init__(self, database_url: str):
        import asyncpg
        self.asyncpg = asyncpg
        self.database_url = database_url
        self.pool = None

    async def init(self) -> None:
        if self.pool is None:
            self.pool = await self.asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
        async with self.pool.acquire() as con:
            await con.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
              session_id TEXT PRIMARY KEY,
              refresh_hash TEXT UNIQUE NOT NULL,
              user_id TEXT NOT NULL,
              tenant_id TEXT NOT NULL,
              org_id TEXT,
              roles JSONB NOT NULL,
              created_at TIMESTAMPTZ DEFAULT NOW(),
              expires_at TIMESTAMPTZ NOT NULL,
              revoked_at TIMESTAMPTZ
            );
            """)

    async def create_session(self, user: User, refresh_token: str) -> str:
        await self.init()
        sid = secrets.token_hex(16)
        rh = _sha256(refresh_token)
        import datetime as _dt
        exp = _dt.datetime.utcnow() + _dt.timedelta(seconds=REFRESH_TTL_S)
        async with self.pool.acquire() as con:
            await con.execute(
                "INSERT INTO sessions(session_id, refresh_hash, user_id, tenant_id, org_id, roles, expires_at) VALUES($1,$2,$3,$4,$5,$6,$7)",
                sid, rh, user.user_id, user.tenant_id, user.org_id, json.dumps(user.roles), exp
            )
        return sid

    async def verify_refresh(self, refresh_token: str) -> Tuple[User, str]:
        await self.init()
        rh = _sha256(refresh_token)
        async with self.pool.acquire() as con:
            row = await con.fetchrow("SELECT * FROM sessions WHERE refresh_hash=$1 AND revoked_at IS NULL", rh)
        if not row:
            raise HTTPException(status_code=401, detail="invalid refresh")
        if row["expires_at"].timestamp() < time.time():
            raise HTTPException(status_code=401, detail="refresh expired")
        user = User(
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            roles=list(row["roles"]) if isinstance(row["roles"], list) else json.loads(row["roles"]),
            org_id=row["org_id"],
        )
        return user, row["session_id"]

    async def revoke_refresh(self, refresh_token: str) -> None:
        await self.init()
        rh = _sha256(refresh_token)
        async with self.pool.acquire() as con:
            await con.execute("UPDATE sessions SET revoked_at=NOW() WHERE refresh_hash=$1", rh)

def build_session_store() -> SessionStore:
    db = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if db:
        return PostgresSessionStore(db)
    return InMemorySessionStore()

SESSION_STORE: SessionStore = build_session_store()

# -----------------------------
# Dependencies
# -----------------------------

def get_current_user(
    authorization: Optional[str] = Header(default=None),
    access_token: Optional[str] = Query(default=None),  # SSE
) -> User:
    tok = access_token
    if not tok and authorization and authorization.lower().startswith("bearer "):
        tok = authorization.split(" ", 1)[1].strip()
    if not tok:
        raise HTTPException(status_code=401, detail="missing bearer token")
    payload = decode_token(tok)
    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="invalid token type")
    return User(
        user_id=payload.get("sub","user"),
        tenant_id=payload.get("tenant_id","tenant_demo"),
        roles=list(payload.get("roles") or []),
        org_id=payload.get("org_id"),
    )
