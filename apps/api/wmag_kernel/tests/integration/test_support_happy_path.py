import pytest

@pytest.mark.asyncio
async def test_support_happy_path_enterprise(kernel, tools):
    payload = {"task_id":"task_1","tenant_id":"tenant_enterprise_eu","user_message":"refund please","metadata":{"customer_id":"cust_123"}}
    events = []
    async for ev in kernel.task_send_subscribe(payload):
        events.append(ev)

    assert any(e.get("state") == "completed" for e in events if e.get("type") == "TaskStatusUpdateEvent")
    assert tools.ticket_create_calls == 1
    assert tools.email_send_calls == 1
