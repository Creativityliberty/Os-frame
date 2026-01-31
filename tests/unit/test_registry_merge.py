import json
from kernel.runtime.policy import apply_tenant_overrides

def test_tenant_override_email_requires_approval():
    reg = json.loads(open("./registry/registry_support_v1.json","r",encoding="utf-8").read())
    reg_demo = apply_tenant_overrides(reg, "tenant_demo")
    a = [x for x in reg_demo["actions"] if x["action_id"] == "act_email_send_v1"][0]
    assert a["security"]["requires_approval"] is True
