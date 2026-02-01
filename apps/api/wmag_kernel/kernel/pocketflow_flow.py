from __future__ import annotations
from typing import Any

try:
    from pocketflow import Flow  # type: ignore
except Exception:  # pragma: no cover
    Flow = None  # type: ignore

from kernel.nodes.receive_task import ReceiveTaskNode
from kernel.nodes.load_tenant import LoadTenantContextNode
from kernel.nodes.load_registry import LoadRegistryNode
from kernel.nodes.load_trees import LoadOrBuildContextTreesNode
from kernel.nodes.select_nodes import SelectNodesTreeSearchNode
from kernel.nodes.hydrate_context import HydrateContextPackNode
from kernel.nodes.plan import PlanWithLLMNode
from kernel.nodes.gate import ValidatePlanAndPolicyGateNode
from kernel.nodes.approval import RequestApprovalNode, WaitForApprovalNode
from kernel.nodes.execute import ExecuteStepsLoopNode
from kernel.nodes.commit import CommitEventsAndProjectionsNode
from kernel.nodes.complete import CompleteRunNode

def build_flow() -> Any:
    if Flow is None:
        raise RuntimeError("pocketflow not installed. Run: pip install pocketflow")

    receive = ReceiveTaskNode()
    tenant = LoadTenantContextNode()
    reg = LoadRegistryNode()
    trees = LoadOrBuildContextTreesNode()
    select = SelectNodesTreeSearchNode()
    hydrate = HydrateContextPackNode()
    plan = PlanWithLLMNode()
    gate = ValidatePlanAndPolicyGateNode()
    req_appr = RequestApprovalNode()
    wait_appr = WaitForApprovalNode()
    exec_steps = ExecuteStepsLoopNode()
    commit = CommitEventsAndProjectionsNode()
    complete = CompleteRunNode()

    receive >> tenant >> reg >> trees >> select >> hydrate >> plan >> gate

    gate - "need_approval" >> req_appr >> wait_appr
    wait_appr - "approved" >> gate
    wait_appr - "denied" >> complete
    wait_appr - "timeout" >> complete

    gate - "ok" >> exec_steps >> commit >> complete

    receive - "fatal" >> complete
    gate - "fatal" >> complete

    return Flow(start=receive)
