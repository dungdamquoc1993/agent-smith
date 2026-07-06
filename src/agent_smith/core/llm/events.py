"""Async event stream for assistant messages."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_smith.core.llm.types import AssistantMessage, AssistantMessageEvent


class AssistantMessageEventStream:
    """
    Async iterable stream that terminates with done/error and exposes .result().

    Mirrors pi's EventStream / AssistantMessageEventStream pattern.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AssistantMessageEvent | None] = asyncio.Queue()
        self._done = False
        self._final_result: AssistantMessage | None = None
        self._complete = asyncio.Event()
        self._producer_coro: Coroutine[Any, Any, None] | None = None
        self._producer_started = False

    def set_producer(self, coro: Coroutine[Any, Any, None]) -> None:
        self._producer_coro = coro

    def push(self, event: AssistantMessageEvent) -> None:
        if self._done:
            return

        if event.type in ("done", "error"):
            self._done = True
            if event.type == "done":
                self._final_result = event.message
            else:
                self._final_result = event.error
            self._complete.set()

        self._queue.put_nowait(event)

    def end(self, result: AssistantMessage | None = None) -> None:
        self._done = True
        if result is not None:
            self._final_result = result
            self._complete.set()
        self._queue.put_nowait(None)

    def _start_producer(self) -> None:
        if self._producer_started or self._producer_coro is None:
            return
        self._producer_started = True
        asyncio.create_task(self._producer_coro)

    def __aiter__(self) -> AsyncIterator[AssistantMessageEvent]:
        return self._iter_events()

    async def _iter_events(self) -> AsyncIterator[AssistantMessageEvent]:
        self._start_producer()
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
            if event.type in ("done", "error"):
                return

    async def result(self) -> AssistantMessage:
        self._start_producer()
        if self._final_result is not None:
            return self._final_result
        await self._complete.wait()
        if self._final_result is None:
            raise RuntimeError("Stream ended without a final assistant message")
        return self._final_result


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    return AssistantMessageEventStream()
