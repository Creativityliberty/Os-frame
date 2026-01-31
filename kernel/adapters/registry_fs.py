from __future__ import annotations
import json, os
from typing import Any, Dict
from kernel.ports.registry import RegistryProvider
from kernel.runtime.policy import apply_tenant_overrides

class FSRegistryProvider(RegistryProvider):
    def __init__(self, path: str):
        self.path = path

    @classmethod
    def from_env(cls) -> "FSRegistryProvider":
        path = os.getenv("REGISTRY_PATH", "./registry/registry_support_v1.json")
        return cls(path)

    async def load_registry(self, tenant_id: str) -> Dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as f:
            reg = json.load(f)
        return apply_tenant_overrides(reg, tenant_id)
