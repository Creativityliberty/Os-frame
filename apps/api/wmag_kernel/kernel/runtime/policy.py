from __future__ import annotations
from typing import Any, Dict, Tuple, List
from kernel.runtime.policy_engine import evaluate_step_policy, compile_effective_limits

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



def policy_gate_plan(reg: Dict[str, Any], tenant_ctx: Dict[str, Any], user_roles: List[str], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Registry-driven policy gate: filters/marks steps. Also collects global obligations."""
    out = dict(plan)
    steps_out = []
    obligations: List[Dict[str, Any]] = []

    for step in (plan.get("steps") or []):
        allowed, patch = evaluate_step_policy(step, user_roles=user_roles, reg=reg, phase="exec")
        if not allowed:
            step2 = dict(step)
            step2["status"] = "denied"
            step2["deny_reason"] = patch.get("deny_reason")
            steps_out.append(step2)
            continue

        step2 = dict(step)
        # propagate patch keys
        for k in ["requires_approval", "cost_units_override", "policy_ids"]:
            if k in patch:
                step2[k] = patch[k]
        steps_out.append(step2)

        # collect obligations
        for ob in (patch.get("obligations") or []):
            if isinstance(ob, dict):
                obligations.append(ob)

    out["steps"] = steps_out

    # merge limits
    tenant_ctx = dict(tenant_ctx or {})
    tenant_ctx["limits"] = compile_effective_limits(tenant_ctx, reg)
    out["tenant_ctx"] = tenant_ctx

    if obligations:
        # de-dup simple
        seen = set()
        uniq = []
        for ob in obligations:
            key = json.dumps(ob, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                uniq.append(ob)
        out["obligations"] = uniq

    return out
