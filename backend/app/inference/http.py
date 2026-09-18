"""Reuse connections within one inference request; retain TLS verification."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
import ssl

import httpx

_clients: ContextVar[dict | None] = ContextVar("inference_clients", default=None)


@lru_cache(maxsize=1)
def _tls_context():
    # Re-reading the Windows certificate store on every LLM/tokenize call is costly.
    return ssl.create_default_context()


@asynccontextmanager
async def inference_client(timeout: float):
    pool = _clients.get()
    if pool is None:
        async with httpx.AsyncClient(timeout=timeout, verify=_tls_context()) as client:
            yield client
        return
    if timeout not in pool:
        pool[timeout] = httpx.AsyncClient(timeout=timeout, verify=_tls_context())
    yield pool[timeout]


def inference_session(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        if _clients.get() is not None:
            return await function(*args, **kwargs)
        pool = {}
        token = _clients.set(pool)
        try:
            return await function(*args, **kwargs)
        finally:
            try:
                for client in pool.values():
                    await client.aclose()
            finally:
                _clients.reset(token)
    return wrapped
