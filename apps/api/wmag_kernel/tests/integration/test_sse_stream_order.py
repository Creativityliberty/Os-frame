import pytest

@pytest.mark.asyncio
async def test_persist_before_yield(kernel, storage):
    payload = {"task_id":"task_5","tenant_id":"tenant_enterprise_eu","user_message":"refund please"}
    async for ev in kernel.task_send_subscribe(payload):
        # The yielded event must already be in storage.updates
        run_id = ev["run_id"]
        assert ev in storage.updates[run_id]
