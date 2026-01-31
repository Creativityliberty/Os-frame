from __future__ import annotations
from typing import Any, Dict, Tuple, List

def _find_action(registry: Dict[str, Any], action_id: str) -> Dict[str, Any]:
    for a in registry.get("actions", []):
        if a.get("action_id") == action_id:
            return a
    raise ValueError(f"Unknown action_id: {action_id}")

def apply_tenant_overrides(registry: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    # Shallow clone; enough for starter
    reg = {**registry}
    overrides = (registry.get("tenant_overrides") or {}).get(tenant_id) or {}
    enabled_tools = set(overrides.get("enabled_tools", [])) or None
    enabled_actions = set(overrides.get("enabled_actions", [])) or None

    if enabled_tools is not None:
        reg["tool_contracts"] = [t for t in registry.get("tool_contracts", []) if t.get("tool") in enabled_tools]
    if enabled_actions is not None:
        reg["actions"] = [a for a in registry.get("actions", []) if a.get("action_id") in enabled_actions]

    # apply security overrides (simple JSON pointer-like patches)
    for so in overrides.get("security_overrides", []):
        aid = so["action_id"]
        patches = so.get("set", [])
        for a in reg["actions"]:
            if a.get("action_id") != aid:
                continue
            for p in patches:
                path = p["path"]
                val = p["value"]
                # Only implement /security/requires_approval and /security/allowed_roles in starter
                if path == "/security/requires_approval":
                    a.setdefault("security", {})["requires_approval"] = bool(val)
                if path == "/security/allowed_roles":
                    a.setdefault("security", {})["allowed_roles"] = list(val)
    return reg

def policy_gate_plan(ctx: Dict[str, Any], plan: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    tenant = ctx["tenant"]
    registry = ctx["registry"]

    needs_approval = bool(plan.get("controls", {}).get("requires_approval", False))
    violations: List[str] = []

    roles = set(tenant.get("roles", []))
    allowed_tools = set(tenant.get("allowed_tools", [])) if tenant.get("allowed_tools") else None
    allowed_actions = set(tenant.get("allowed_actions", [])) if tenant.get("allowed_actions") else None

    for step in plan.get("steps", []):
        aid = step["action_id"]
        if allowed_actions is not None and aid not in allowed_actions:
            violations.append(f"Action not allowed: {aid}")
            continue
        a = _find_action(registry, aid)
        tool = a.get("tool")
        if allowed_tools is not None and tool not in allowed_tools:
            violations.append(f"Tool not allowed: {tool}")
        ar = set(a.get("security", {}).get("allowed_roles", []) or [])
        if ar and roles.isdisjoint(ar):
            violations.append(f"Role mismatch for action {aid}")
        if a.get("security", {}).get("requires_approval", False):
            needs_approval = True

    ok = len(violations) == 0
    report = {"ok": ok, "needs_approval": needs_approval, "violations": violations}
    if not ok:
        report["fatal"] = True
    return needs_approval, report
