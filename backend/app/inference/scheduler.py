import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class InferenceScheduler:
    """V3.3 fusion execution gate (capacity 1).

    It no longer serializes the two Agents inside one fusion: with two
    resident llama-servers every Agent targets its own endpoint concurrently.
    It only guarantees that different fusion Jobs run one at a time, keeping
    cross-Job behaviour deterministic and resource use bounded.
    """

    def __init__(self) -> None:
        self._gate = asyncio.Semaphore(1)

    async def run_serial(
        self, operation: Callable[[], Awaitable[T]], on_acquired: Callable[[], None] | None = None
    ) -> T:
        async with self._gate:
            if on_acquired is not None:
                on_acquired()
            return await operation()


inference_scheduler = InferenceScheduler()
