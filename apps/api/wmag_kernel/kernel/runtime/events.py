from __future__ import annotations
import time
from typing import Any, Dict, Optional

RUN_STATES = {"submitted","working","input-required","completed","failed","canceled"}

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def status_event(task_id: str, run_id: str, state: str, message: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    assert state in RUN_STATES
    return {"type":"TaskStatusUpdateEvent","ts":now_iso(),"task_id":task_id,"run_id":run_id,"state":state,"message":message,"meta":meta or {}}

def artifact_event(task_id: str, run_id: str, artifact_type: str, artifact: Dict[str, Any]) -> Dict[str, Any]:
    return {"type":"TaskArtifactUpdateEvent","ts":now_iso(),"task_id":task_id,"run_id":run_id,"artifact_type":artifact_type,"artifact":artifact}


def budget_event(task_id: str, run_id: str, used: Dict[str, Any], limits: Dict[str, Any]) -> Dict[str, Any]:
    return {"type":"TaskBudgetUpdateEvent","ts":now_iso(),"task_id":task_id,"run_id":run_id,"used":used,"limits":limits}
