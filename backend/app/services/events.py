from __future__ import annotations

from collections.abc import Callable
from typing import Any
from app.services.timing import phase as timed_phase, timing

EventSink = Callable[[dict[str, Any]], None]


def emit(sink: EventSink | None, event_type: str, phase: str, title: str, **detail: Any) -> None:
    if detail.get("duration_ms") is not None and event_type != "model_ready":
        timing(phase, detail["duration_ms"], event=event_type,
               model=detail.get("model"), status=detail.get("status"))
    if sink is not None:
        with timed_phase("event_persist", event=event_type):
            sink({"event_type": event_type, "phase": phase, "title": title, **detail})


__all__ = ["EventSink", "emit"]
