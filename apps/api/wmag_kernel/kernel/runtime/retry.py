from __future__ import annotations
import asyncio
from typing import Any, Dict, List, Tuple, Callable, Awaitable

from kernel.runtime.errors import NON_RETRYABLE

async def run_with_retry(
    *,
    call: Callable[[], Awaitable[Dict[str, Any]]],
    classify_error: Callable[[Exception], str],
    retry_cfg: Dict[str, Any],
) -> Tuple[Dict[str, Any] | None, Dict[str, Any] | None, int]:
    max_attempts = int(retry_cfg.get("max_attempts", 1))
    backoff_ms = list(retry_cfg.get("backoff_ms", []))
    retry_on = set(retry_cfg.get("retry_on", []))
    attempts = 0
    last_error = None

    while attempts < max_attempts:
        attempts += 1
        try:
            out = await call()
            return out, None, attempts
        except Exception as exc:
            err_class = classify_error(exc)
            last_error = {"class": err_class, "message": str(exc)}
            if err_class in NON_RETRYABLE:
                return None, last_error, attempts
            if err_class not in retry_on or attempts >= max_attempts:
                return None, last_error, attempts

            delay = 0.25
            if backoff_ms:
                idx = min(attempts - 1, len(backoff_ms) - 1)
                delay = backoff_ms[idx] / 1000.0
            await asyncio.sleep(delay)

    return None, last_error, attempts
