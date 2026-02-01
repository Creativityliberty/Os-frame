import pytest

@pytest.mark.asyncio
async def test_rate_limit_retry(kernel, tools):
    tools.rate_limit_first_email = True
    payload = {"task_id":"task_3","tenant_id":"tenant_enterprise_eu","user_message":"refund please","metadata":{"customer_id":"cust_123"}}
    step_results = []
    async for ev in kernel.task_send_subscribe(payload):
        if ev.get("type") == "TaskArtifactUpdateEvent" and ev.get("artifact_type") == "step_result":
            step_results.append(ev["artifact"])

    s6 = [r for r in step_results if r["step_id"] == "s6"][0]
    assert s6["status"] == "succeeded"
    assert s6["attempts"] == 2
    assert tools.email_send_calls == 2
