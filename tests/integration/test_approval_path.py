import pytest

@pytest.mark.asyncio
async def test_approval_path_demo(kernel, storage, tools):
    # demo tenant requires approval for email; storage auto-approves in starter
    payload = {"task_id":"task_2","tenant_id":"tenant_demo","user_message":"refund please","metadata":{"customer_id":"cust_123"}}
    events = []
    async for ev in kernel.task_send_subscribe(payload):
        events.append(ev)

    # should have input-required, then continue to completed (auto-approve)
    states = [e["state"] for e in events if e.get("type") == "TaskStatusUpdateEvent"]
    assert "input-required" in states
    assert "completed" in states
    assert tools.email_send_calls == 1
