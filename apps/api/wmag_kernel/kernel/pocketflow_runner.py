from __future__ import annotations
from typing import Any, AsyncIterator, Dict

async def run_streaming(start_node: Any, shared: Dict[str, Any]) -> AsyncIterator[dict]:
    """Execute a PocketFlow graph node-by-node and yield SSE events.

    Nodes are expected to be AsyncNode-like:
      - prep_async(shared) -> prep
      - exec_async(prep) -> out
      - post_async(shared, prep, out) -> action (str)

    Transitions are expected in node.successors: {action: next_node, "default": next_node}
    Streaming:
      - Nodes must persist-before-send and append events into shared["stream_queue"].
    """
    node = start_node
    while node is not None:
        prep = await node.prep_async(shared)
        out = await node.exec_async(prep)
        action = await node.post_async(shared, prep, out)

        q = shared.get("stream_queue") or []
        while q:
            yield q.pop(0)

        succ = getattr(node, "successors", None) or {}
        node = succ.get(action) or succ.get("default")
