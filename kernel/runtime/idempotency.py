from __future__ import annotations
import hashlib, json
from typing import Any, Dict

def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def compute_idempotency_key(*, tenant_id: str, run_id: str, step_id: str, action_id: str, args: Dict[str, Any]) -> str:
    payload = {"tenant_id": tenant_id, "run_id": run_id, "step_id": step_id, "action_id": action_id, "args": args}
    h = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return "idem_" + h[:32]
