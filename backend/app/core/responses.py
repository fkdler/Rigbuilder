"""UTF-8-safe response classes.

FastAPI's default ``JSONResponse`` sets ``Content-Type: application/json``
without a charset.  Per RFC 8259 JSON is UTF-8, but some browsers and
EventSource implementations default to Latin-1 when the header omits the
charset, causing 乱码.  These classes explicitly declare UTF-8.
"""

from json import dumps
from typing import Any

from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse


class UTF8JSONResponse(JSONResponse):
    """JSONResponse with ``charset=utf-8`` in the Content-Type header."""

    media_type = "application/json; charset=utf-8"

    def render(self, content: Any) -> bytes:
        return dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


class UTF8StreamingResponse(StreamingResponse):
    """StreamingResponse with ``charset=utf-8`` for SSE."""

    def __init__(self, *args: Any, media_type: str = "text/event-stream; charset=utf-8", **kwargs: Any) -> None:
        super().__init__(*args, media_type=media_type, **kwargs)


__all__ = ["UTF8JSONResponse", "UTF8StreamingResponse"]
