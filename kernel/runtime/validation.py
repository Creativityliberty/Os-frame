from __future__ import annotations
from typing import Any, Dict
from jsonschema import validate
import json

from pathlib import Path

def validate_task_input(task_input: Dict[str, Any]) -> None:
    required = {"task_id","tenant_id","user_message"}
    missing = required - set(task_input.keys())
    if missing:
        raise ValueError(f"Missing keys: {sorted(missing)}")

def validate_plan(plan: Dict[str, Any], schema_path: str) -> None:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validate(instance=plan, schema=schema)
