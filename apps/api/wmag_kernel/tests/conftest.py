import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from kernel.flow import Kernel
from kernel.adapters.storage_inmemory import InMemoryStorage
from kernel.adapters.registry_fs import FSRegistryProvider
from kernel.adapters.index_inmemory import InMemoryIndexProvider
from kernel.adapters.planner_llm_stub import StubPlanner
from kernel.adapters.hydrator_stub import StubHydrator
from kernel.adapters.toolrunner_stub import StubToolRunner

@pytest.fixture
def storage():
    return InMemoryStorage()

@pytest.fixture
def tools():
    return StubToolRunner()

@pytest.fixture
def registry_provider():
    return FSRegistryProvider("./registry/registry_support_v1.json")

@pytest.fixture
def kernel(storage, tools, registry_provider):
    return Kernel(
        storage=storage,
        registry=registry_provider,
        index=InMemoryIndexProvider(),
        planner=StubPlanner(),
        hydrator=StubHydrator(),
        tools=tools,
    )
