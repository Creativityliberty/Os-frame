from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
import json

from kernel.flow import Kernel
from kernel.adapters.storage_inmemory import InMemoryStorage
from kernel.adapters.registry_fs import FSRegistryProvider
from kernel.adapters.index_inmemory import InMemoryIndexProvider
from kernel.adapters.planner_llm_stub import StubPlanner
from kernel.adapters.hydrator_stub import StubHydrator
from kernel.adapters.toolrunner_stub import StubToolRunner

router = APIRouter()

def get_kernel() -> Kernel:
    return Kernel(
        storage=InMemoryStorage(),
        registry=FSRegistryProvider("./registry/registry_support_v1.json"),
        index=InMemoryIndexProvider(),
        planner=StubPlanner(),
        hydrator=StubHydrator(),
        tools=StubToolRunner(),
    )

def sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

@router.post("/v1/tasks:sendSubscribe")
async def send_subscribe(payload: dict, kernel: Kernel = Depends(get_kernel)):
    async def gen():
        async for ev in kernel.task_send_subscribe(payload):
            yield sse(ev)
    return StreamingResponse(gen(), media_type="text/event-stream")
