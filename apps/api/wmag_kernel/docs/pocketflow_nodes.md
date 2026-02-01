# PocketFlow Nodes mapping (WMAG Kernel)

This is the **explicit mapping** of WMAG kernel phases to PocketFlow nodes.

## Flow shape

```
[IngestTask] -> [LoadContext] -> [SelectWorldNodes] -> [Plan] -> [GateApproval] -> [ExecuteSteps] -> [Synthesize] -> [Complete]
                                              \-> (failed) ------------------------------> [Fail]
```

## Node responsibilities

### 1) IngestTask
- prep: validate task input
- exec: normalize, assign run_id, init state=submitted
- post: persist TaskStatusUpdateEvent

### 2) LoadContext
- prep: load tenant context, limits
- exec: read registry + policies
- post: persist status=working

### 3) SelectWorldNodes
- prep: take user message, domains
- exec: call WorldIndexProvider to fetch relevant nodes/trees
- post: persist artifact=context_pack

### 4) Plan
- prep: context_pack
- exec: call Planner to build plan JSON
- post: persist artifact=plan

### 5) GateApproval
- prep: plan.controls.requires_approval
- exec: if true => emit input-required + wait approval
- post: if approved -> ok, else failed/canceled

### 6) ExecuteSteps
- prep: plan.steps
- exec: for each step:
  - compute idempotency key
  - cache check
  - run_with_retry(toolrunner.run)
  - persist step_result artifact
- post: status=working (progress), then ok

### 7) Synthesize
- prep: step_results
- exec: draft final response / final artifact
- post: artifact=final + status=completed

### 8) Fail
- persist failed status with error taxonomy

## Where it lives
- Kernel flow code: `apps/api/wmag_kernel/kernel/flow.py`
- Step runner: `apps/api/wmag_kernel/kernel/adapters/storage_inmemory.py` (demo)
