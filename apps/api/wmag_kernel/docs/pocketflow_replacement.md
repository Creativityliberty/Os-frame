# PocketFlow Replacement Guide (Full)

Ce guide te montre **comment remplacer les stubs** `kernel/nodes/*.py` par de **vrais Nodes PocketFlow** (et comment câbler le flow),
tout en gardant l’architecture WMAG: `Registry → Gate → Execute → Stream → Replay`.

## Installer
```bash
pip install pocketflow
```

## Signatures
Node:
```python
from pocketflow import Node
class MyNode(Node):
  def prep(self, shared): ...
  def exec(self, prep_res): ...
  def post(self, shared, prep_res, exec_res): return "default"
```

AsyncNode:
```python
from pocketflow import AsyncNode
class MyAsync(AsyncNode):
  async def prep_async(self, shared): ...
  async def exec_async(self, prep_res): ...
  async def post_async(self, shared, prep_res, exec_res): return "default"
```

## Transitions
- default: `A >> B`
- action: `A - "ok" >> B`

## Shared keys canon
- shared.task / shared.run / shared.deps
- shared.registry / shared.tenant / shared.trees
- shared.node_selection / shared.context_pack / shared.plan
- shared.gate_report / shared.step_results
- shared.stream_queue (events SSE)

## Wiring example
Crée `kernel/pocketflow_flow.py` et câble les nodes via `>>` et `- "action" >>`.
Voir aussi `docs/design.md` (v3) pour le flow complet.

## Streaming SSE (runner)
Crée `kernel/pocketflow_runner.py`:

```python
async def run_streaming(start_node, shared):
  node = start_node
  while node:
    prep = await node.prep_async(shared)
    out = await node.exec_async(prep)
    action = await node.post_async(shared, prep, out)
    q = shared.get("stream_queue") or []
    while q: yield q.pop(0)
    succ = getattr(node, "successors", {}) or {}
    node = succ.get(action) or succ.get("default")
```

## Persist-before-send
Toujours:
1) storage.persist_update(run_id, ev)
2) shared.stream_queue.append(ev)

## Migration safe
1) ReceiveTask + Gate en PocketFlow
2) ExecuteSteps reste wrapper
3) migrate node-by-node

---

## PocketFlow runtime files now in repo

- `kernel/pocketflow_flow.py` (Flow wiring)
- `kernel/pocketflow_runner.py` (streaming runner)
- `kernel/nodes/_helpers.py` (`emit()` persist-before-send)
