# WMAG Kernel Starter (PocketFlow-oriented)

This is a starter repo for a **WMAG Kernel** (World Model + Action Graph) with:
- Multi-tenant Action Registry
- Deterministic runtime execution (idempotency, retry, policy gate)
- SSE-friendly streaming updates (status + artifact)
- Replay safety (no duplicated side effects)
- Test harness (unit + integration)

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pytest -q
```

## Notes
- This starter uses **stubs/in-memory adapters** so tests pass without external services.
- Replace adapters under `kernel/adapters/` with real implementations (Postgres, real LLM, real tools).


PocketFlow replacement guide:
- `docs/pocketflow_replacement.md`


PocketFlow runtime:
- `kernel/pocketflow_flow.py`
- `kernel/pocketflow_runner.py`

## PocketFlow mode (auto)

If `pocketflow` is installed, `Kernel.task_send_subscribe()` will automatically run the PocketFlow graph:
- `kernel/pocketflow_flow.py` (graph wiring)
- `kernel/pocketflow_runner.py` (streaming runner)

Otherwise it falls back to the legacy sequential pipeline.

Install:
```bash
pip install pocketflow
```
