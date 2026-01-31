import json
from kernel.runtime.policy import apply_tenant_overrides, policy_gate_plan

def test_policy_gate_needs_approval_for_demo():
    base = json.loads(open("./registry/registry_support_v1.json","r",encoding="utf-8").read())
    reg = apply_tenant_overrides(base, "tenant_demo")
    tenant = {"tenant_id":"tenant_demo","roles":["support_agent"]}
    plan = {"type":"plan","goal":"x","steps":[{"step_id":"s6","action_id":"act_email_send_v1","args":{"to":"a@b.com","subject":"x","body":"y","idempotency_key":"idem"}}]}
    needs, report = policy_gate_plan({"tenant":tenant,"registry":reg}, plan)
    assert report["ok"] is True
    assert needs is True
