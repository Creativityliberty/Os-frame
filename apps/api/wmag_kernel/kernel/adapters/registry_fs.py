from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from kernel.ports.registry import RegistryProvider


def deep_merge(a: Any, b: Any) -> Any:
    """Deep merge b into a (returns new). Dicts merge recursively, lists replaced by default."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            if k in out:
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = deepcopy(v)
        return out
    # For lists we generally replace (overrides should be explicit)
    return deepcopy(b)

def merge_indexed_list(base_list: list, override_list: list, key: str) -> list:
    """Merge lists of dicts by key field; override replaces matching items."""
    idx = {x.get(key): deepcopy(x) for x in (base_list or []) if isinstance(x, dict) and key in x}
    for item in (override_list or []):
        if not isinstance(item, dict) or key not in item:
            continue
        idx[item[key]] = deep_merge(idx.get(item[key], {}), item)
    # stable order: base order then new
    out = []
    seen=set()
    for x in (base_list or []):
        if isinstance(x, dict) and key in x and x[key] in idx:
            out.append(idx[x[key]])
            seen.add(x[key])
    for k,v in idx.items():
        if k not in seen:
            out.append(v)
    return out

def apply_overrides(reg: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Apply override to base registry with smarter merging for tools/actions/policies."""
    out = deepcopy(reg)
    for section in ["tools", "actions", "policies"]:
        if section in override:
            if isinstance(out.get(section), list) and isinstance(override[section], list):
                key = "tool_id" if section == "tools" else ("action_id" if section == "actions" else "policy_id")
                out[section] = merge_indexed_list(out.get(section, []), override.get(section, []), key)
            else:
                out[section] = deep_merge(out.get(section), override.get(section))
    # merge roles map
    if "roles" in override:
        out["roles"] = deep_merge(out.get("roles", {}), override["roles"])
    # merge other fields
    for k, v in override.items():
        if k in ("tools","actions","policies","roles"):
            continue
        out[k] = deep_merge(out.get(k), v)
    return out


class FSRegistryProvider(RegistryProvider):
    def __init__(self, path: str):
        self.path = path

    @classmethod
    def from_env(cls) -> "FSRegistryProvider":
        return cls(os.getenv("REGISTRY_PATH", "./wmag_kernel/registry/registry_support_v1.json"))

    def _read_json(self, p: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _override_paths(self, task: Dict[str, Any]) -> list[Path]:
        base_dir = Path(os.getenv("REGISTRY_LAYERS_DIR", "./config"))
        org_id = task.get("org_id") or task.get("org") or task.get("tenant", {}).get("org_id")
        tenant_id = task.get("tenant_id")
        user_id = task.get("user_id")
        paths: list[Path] = []
        if org_id:
            paths.append(base_dir / "orgs" / org_id / "registry_override.json")
        if tenant_id:
            paths.append(base_dir / "tenants" / tenant_id / "registry_override.json")
        if user_id:
            paths.append(base_dir / "users" / user_id / "registry_override.json")
        return paths

    async def load_registry_for(self, task: Dict[str, Any]) -> Dict[str, Any]:
        base = self._read_json(Path(self.path)) or {"registry_id": "missing", "schema_version": "0"}
        out = deepcopy(base)
        for p in self._override_paths(task):
            if p.exists():
                ov = self._read_json(p)
                if ov:
                    out = apply_overrides(out, ov)
        return out

    async def load_registry(self, tenant_id: str) -> Dict[str, Any]:
        # Back-compat path: apply tenant override if present
        task = {"tenant_id": tenant_id}
        return await self.load_registry_for(task)
