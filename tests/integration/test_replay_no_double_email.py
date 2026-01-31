import pytest

@pytest.mark.asyncio
async def test_replay_no_double_email(kernel, tools):
    # First run crashes after sending email step (simulated in storage.execute_plan)
    payload1 = {"task_id":"task_4","tenant_id":"tenant_enterprise_eu","user_message":"refund please","metadata":{"customer_id":"cust_123","crash_after_step":"s6"}}
    events1 = []
    async for ev in kernel.task_send_subscribe(payload1):
        events1.append(ev)

    # It should fail due to crash
    assert any(e.get("state") == "failed" for e in events1 if e.get("type") == "TaskStatusUpdateEvent")
    assert tools.email_send_calls == 1

    # Replay: run again without crash. Idempotency cache should prevent second email call.
    payload2 = {"task_id":"task_4","tenant_id":"tenant_enterprise_eu","user_message":"refund please","metadata":{"customer_id":"cust_123"}}
    events2 = []
    async for ev in kernel.task_send_subscribe(payload2):
        events2.append(ev)

    assert any(e.get("state") == "completed" for e in events2 if e.get("type") == "TaskStatusUpdateEvent")
    assert tools.email_send_calls == 1
