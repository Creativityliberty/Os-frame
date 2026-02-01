from kernel.runtime.idempotency import compute_idempotency_key

def test_idempotency_stable():
    k1 = compute_idempotency_key(tenant_id="t", run_id="r", step_id="s1", action_id="a", args={"x":1})
    k2 = compute_idempotency_key(tenant_id="t", run_id="r", step_id="s1", action_id="a", args={"x":1})
    assert k1 == k2

def test_idempotency_changes_on_args():
    k1 = compute_idempotency_key(tenant_id="t", run_id="r", step_id="s1", action_id="a", args={"x":1})
    k2 = compute_idempotency_key(tenant_id="t", run_id="r", step_id="s1", action_id="a", args={"x":2})
    assert k1 != k2
