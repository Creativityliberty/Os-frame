from __future__ import annotations
from typing import Any, Dict

async def emit(shared: Dict[str, Any], ev: Dict[str, Any]) -> None:
    """Persist-before-send helper."""
    storage = shared["deps"]["storage"]
    run_id = shared["run"]["run_id"]
    await storage.persist_update(run_id, ev)
    shared.setdefault("stream_queue", []).append(ev)
