"""Runtime-only HTTP dependencies and streaming helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from agent_smith.app.services.agent_runs import AgentRunEventSink
from agent_smith.bootstrap.runtime_http import RuntimeHttpContainer
from agent_smith.transports.runtime_http.sse import sse_chunk

SseQueueItem = dict[str, Any] | None
SseRunner = Callable[[AgentRunEventSink], Awaitable[None]]


def get_container(request: Request) -> RuntimeHttpContainer:
    return request.app.state.container


def sse_response(runner: SseRunner) -> StreamingResponse:
    return StreamingResponse(
        _sse_iterator(runner),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def _sse_iterator(runner: SseRunner) -> AsyncIterator[bytes]:
    events: asyncio.Queue[SseQueueItem] = asyncio.Queue()

    async def emit(event: str, data: Any) -> None:
        await events.put({"event": event, "data": data})

    async def run() -> None:
        try:
            await runner(emit)
        finally:
            await events.put(None)

    task = asyncio.create_task(run())
    try:
        while True:
            item = await events.get()
            if item is None:
                break
            yield sse_chunk(str(item.get("event") or "message"), item.get("data"))
        await task
    except asyncio.CancelledError:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        raise
