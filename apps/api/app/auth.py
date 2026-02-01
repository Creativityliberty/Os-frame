from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, HTTPException, Header, Query

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(Path(__file__).resolve().parents[1] / "config")))
USERS_FILE = CONFIG_DIR / "users.json"

@dataclass
class User:
    user_id: str
    tenant_id: str
    roles: List[str]

def load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {"keys": []}

def _match_key(api_key: str) -> Optional[User]:
    cfg = load_users()
    for rec in cfg.get("keys", []):
        if rec.get("api_key") == api_key:
            return User(
                user_id=rec.get("user_id","user"),
                tenant_id=rec.get("tenant_id","tenant_demo"),
                roles=list(rec.get("roles") or []),
            )
    return None

def get_current_user(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    api_key: Optional[str] = Query(default=None),  # for SSE/EventSource
) -> User:
    key = x_api_key or api_key
    if not key:
        raise HTTPException(status_code=401, detail="missing api key (X-API-Key header or api_key query param)")
    u = _match_key(key)
    if not u:
        raise HTTPException(status_code=401, detail="invalid api key")
    return u

def require_roles(*required: str):
    def dep(user: User = Depends(get_current_user)) -> User:
        if required:
            have=set(user.roles)
            need=set(required)
            if have.isdisjoint(need):
                raise HTTPException(status_code=403, detail=f"missing role(s): {', '.join(required)}")
        return user
    return dep
