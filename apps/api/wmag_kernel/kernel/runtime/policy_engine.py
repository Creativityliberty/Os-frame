from __future__ import annotations

import fnmatch
from typing import Any, Dict, List, Tuple


def _match(val: str, pat: str) -> bool:
    return val == pat or fnmatch.fnmatch(val, pat)


def compile_effective_limits(tenant: Dict[str, Any], reg: Dict[str, Any]) -> Dict[str, Any]:
    base = dict((tenant or {}).get("limits") or {})
    ov = dict((reg or {}).get("limits") or {})
    base.update(ov)
    return base


def _normalize_policies(reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pol in (reg.get("policies") or []):
        if isinstance(pol, dict) and "when" in pol and "effect" in pol:
            out.append(pol)
            continue
    out.sort(key=lambda p: int(p.get("priority", 0)), reverse=True)
    return out


def _cond_matches(cond: Dict[str, Any], step: Dict[str, Any], user_roles: List[str]) -> bool:
    """Composable conditions:
    - {action:"send_email"} / {tool:"mcp:*"}
    - {roles_any:[...]} / {roles_all:[...]}
    - {all:[cond,cond]} / {any:[cond,...]} / {not:cond}
    """
    if not isinstance(cond, dict):
        return False

    # composition
    if "all" in cond:
        arr = cond.get("all") or []
        return all(_cond_matches(c, step, user_roles) for c in arr)
    if "any" in cond:
        arr = cond.get("any") or []
        return any(_cond_matches(c, step, user_roles) for c in arr)
    if "not" in cond:
        return not _cond_matches(cond.get("not") or {}, step, user_roles)

    action_id = str(step.get("action_id", "") or "")
    tool = str(step.get("tool") or step.get("tool_id") or "")

    if cond.get("action") and not _match(action_id, str(cond["action"])):
        return False
    if cond.get("tool") and not _match(tool, str(cond["tool"])):
        return False

    if cond.get("roles_any"):
        if not set(user_roles).intersection(set(cond.get("roles_any") or [])):
            return False
    if cond.get("roles_all"):
        if not set(cond.get("roles_all") or []).issubset(set(user_roles)):
            return False

    return True


def evaluate_step_policy(step: Dict[str, Any], user_roles: List[str], reg: Dict[str, Any], phase: str) -> Tuple[bool, Dict[str, Any]]:
    patch: Dict[str, Any] = {}

    # Step-local RBAC (still allowed)
    sec = (step.get("security") or {})
    allowed_roles = sec.get("allowed_roles")
    if allowed_roles and not set(user_roles).intersection(set(allowed_roles)):
        patch["deny_reason"] = {"class": "RBAC", "message": "role not allowed"}
        return False, patch
    if sec.get("requires_approval"):
        patch["requires_approval"] = True

    obligations: List[Dict[str, Any]] = []
    matched_policy_ids: List[str] = []

    for rule in _normalize_policies(reg):
        if rule.get("phase") and rule.get("phase") != phase:
            continue
        when = rule.get("when") or {}
        if not _cond_matches(when, step, user_roles):
            continue

        matched_policy_ids.append(str(rule.get('policy_id','policy')))
        eff = rule.get("effect") or {}

        if eff.get("deny"):
            patch["deny_reason"] = eff.get("deny_reason") or {"class": "POLICY", "message": f"denied by {rule.get('policy_id','policy')}"}
            return False, patch

        if eff.get("require_approval") is True:
            patch["requires_approval"] = True

        if isinstance(eff.get("set_cost_units"), int):
            patch["cost_units_override"] = int(eff["set_cost_units"])

        # obligations: enforce at workflow level (after execution)
        if isinstance(eff.get("obligations"), list):
            for ob in eff["obligations"]:
                if isinstance(ob, dict):
                    obligations.append(ob)

    if matched_policy_ids:
        patch["policy_ids"] = matched_policy_ids

    if obligations:
        patch["obligations"] = obligations

    return True, patch
