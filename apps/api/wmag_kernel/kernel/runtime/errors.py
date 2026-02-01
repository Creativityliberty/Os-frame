from __future__ import annotations
from typing import Set

ERROR_AUTH = "AUTH"
ERROR_PERMISSION = "PERMISSION"
ERROR_RATE_LIMIT = "RATE_LIMIT"
ERROR_VALIDATION = "VALIDATION"
ERROR_NOT_FOUND = "NOT_FOUND"
ERROR_CONFLICT = "CONFLICT"
ERROR_TRANSIENT = "TRANSIENT"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_UPSTREAM = "UPSTREAM"
ERROR_UNKNOWN = "UNKNOWN"

NON_RETRYABLE: Set[str] = {ERROR_AUTH, ERROR_PERMISSION, ERROR_VALIDATION}

def classify_error(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "unauthorized" in msg or "auth" in name:
        return ERROR_AUTH
    if "forbidden" in msg or "permission" in msg:
        return ERROR_PERMISSION
    if "rate" in msg or "429" in msg:
        return ERROR_RATE_LIMIT
    if "timeout" in msg:
        return ERROR_TIMEOUT
    if "not found" in msg or "404" in msg:
        return ERROR_NOT_FOUND
    if "conflict" in msg or "409" in msg:
        return ERROR_CONFLICT
    if "validation" in msg or "invalid" in msg:
        return ERROR_VALIDATION
    if "upstream" in msg or "5xx" in msg:
        return ERROR_UPSTREAM
    if "network" in msg or "connection" in name:
        return ERROR_TRANSIENT
    return ERROR_UNKNOWN
