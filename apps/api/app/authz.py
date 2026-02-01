from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException

from app.auth_jwt import User, get_current_user

REGISTRY_PATH = Path(os.getenv("REGISTRY_PATH", "./wmag_kernel/registry/registry_support_v1.json"))

_cache = {"mtime": 0.0, "roles": {}}

def _load_roles() -> Dict[str, List[str]]:
    try:
        mtime = REGISTRY_PATH.stat().st_mtime
    except Exception:
        return {}

    if _cache["mtime"] != mtime:
        try:
            reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            roles = reg.get("roles") or {}
            # accept list or dict format
            if isinstance(roles, list):
                roles = {r.get("id"): r.get("capabilities", []) for r in roles}
            _cache["roles"] = roles or {}
            _cache["mtime"] = mtime
        except Exception:
            _cache["roles"] = {}
            _cache["mtime"] = mtime
    return _cache["roles"]

def has_capability(user: User, cap: str) -> bool:
    roles_map = _load_roles()
    for role in user.roles:
        caps = roles_map.get(role) or []
        if cap in caps or "*" in caps:
            return True
    return False

def require_capability(cap: str):
    def dep(user: User = Depends(get_current_user)) -> User:
        if not has_capability(user, cap):
            raise HTTPException(status_code=403, detail=f"missing capability: {cap}")
        return user
    return dep
