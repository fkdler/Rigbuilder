"""Request-correlated operational timings, without prompts, SQL or credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import logging
import time
from uuid import uuid4

logger = logging.getLogger("uvicorn.error")
request_context: ContextVar[str] = ContextVar("timing_request", default="-")


def timing(stage: str, duration_ms: float, **fields) -> None:
    values = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    logger.info("[timing] request=%s stage=%s ms=%.1f %s", request_context.get(), stage, duration_ms, values)


@contextmanager
def phase(stage: str, **fields):
    started = time.perf_counter()
    status = "completed"
    try:
        yield
    except BaseException:
        status = "failed_or_cancelled"
        raise
    finally:
        timing(stage, (time.perf_counter() - started) * 1000, status=status, **fields)


def traced_request(function):
    """Set one ID for both workers and all their nested gateway/SQL operations."""
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        identity = bound.arguments.get("request_id") or uuid4()
        bound.arguments["request_id"] = identity
        token = request_context.set(str(identity))
        try:
            with phase("fusion_total"):
                return await function(*bound.args, **bound.kwargs)
        finally:
            request_context.reset(token)
    return wrapped
