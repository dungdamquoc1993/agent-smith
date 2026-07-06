"""Async event stream for agent loop events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from typing import Any

from agent_smith.core.agent.types import AgentEvent, AgentMessage


class AgentEventStream:
    """Async iterable stream that terminates with agent_end and exposes .result()."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._done = False
        self._final_result: list[AgentMessage] | None = None
        self._exception: BaseException | None = None
        self._complete = asyncio.Event()
        self._producer_coro: Coroutine[Any, Any, None] | None = None
        self._producer_started = False

    def set_producer(self, coro: Coroutine[Any, Any, None]) -> None:
        self._producer_coro = coro

    def push(self, event: AgentEvent) -> None:
        if self._done:
            return

        if event.type == "agent_end":
            self._done = True
            self._final_result = event.messages
            self._complete.set()

        self._queue.put_nowait(event)

    def end(self, result: list[AgentMessage] | None = None) -> None:
        self._done = True
        if result is not None:
            self._final_result = result
            self._complete.set()
        self._queue.put_nowait(None)

    def fail(self, error: BaseException) -> None:
        self._done = True
        self._exception = error
        self._complete.set()
        self._queue.put_nowait(None)

    def _start_producer(self) -> None:
        if self._producer_started or self._producer_coro is None:
            return
        self._producer_started = True
        asyncio.create_task(self._producer_coro)

    def __aiter__(self) -> AsyncIterator[AgentEvent]:
        return self._iter_events()

    async def _iter_events(self) -> AsyncIterator[AgentEvent]:
        self._start_producer()
        while True:
            event = await self._queue.get()
            if event is None:
                if self._exception is not None:
                    raise self._exception
                return
            yield event
            if event.type == "agent_end":
                return

    async def result(self) -> list[AgentMessage]:
        self._start_producer()
        if self._final_result is not None:
            return self._final_result
        await self._complete.wait()
        if self._exception is not None:
            raise self._exception
        if self._final_result is None:
            raise RuntimeError("Stream ended without final agent messages")
        return self._final_result


def create_agent_event_stream() -> AgentEventStream:
    return AgentEventStream()
