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
