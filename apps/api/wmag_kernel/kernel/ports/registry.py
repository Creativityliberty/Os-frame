from __future__ import annotations
from typing import Any, Dict, Protocol

class RegistryProvider(Protocol):
    async def load_registry(self, tenant_id: str) -> Dict[str, Any]: ...
