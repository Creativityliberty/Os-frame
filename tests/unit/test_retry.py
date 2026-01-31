import pytest
from kernel.runtime.retry import run_with_retry
from kernel.runtime.errors import ERROR_RATE_LIMIT

class RateLimit(Exception): ...

def classify(e: Exception) -> str:
    return ERROR_RATE_LIMIT

@pytest.mark.asyncio
async def test_retry_happens():
    attempts = {"n": 0}
    async def call():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RateLimit("429 rate limit")
        return {"ok": True}

    out, err, n = await run_with_retry(call=call, classify_error=classify, retry_cfg={"max_attempts":2,"backoff_ms":[1],"retry_on":[ERROR_RATE_LIMIT]})
    assert err is None
    assert out == {"ok": True}
    assert n == 2
