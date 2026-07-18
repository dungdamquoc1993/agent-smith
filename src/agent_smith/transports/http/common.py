"""Shared helpers for the FastAPI HTTP transport."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from http import HTTPStatus
from typing import Any

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent_smith.bootstrap.http import HttpContainer
from agent_smith.app.services.agent_runs import AgentRunEventSink
from agent_smith.transports.http.sse import jsonable, sse_chunk

SseQueueItem = dict[str, Any] | None
SseRunner = Callable[[AgentRunEventSink], Awaitable[None]]


class AgentSmithHttpError(Exception):
    def __init__(
        self,
        status_code: HTTPStatus | int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = code
        self.message = message
        self.headers = headers or {}


def get_container(request: Request) -> HttpContainer:
    return request.app.state.container


def json_response(data: Any, *, status_code: HTTPStatus | int = HTTPStatus.OK) -> JSONResponse:
    return JSONResponse(content=jsonable(data), status_code=int(status_code))


def error_response(
    status_code: HTTPStatus | int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        content=jsonable({"error": {"code": code, "message": message}}),
        status_code=int(status_code),
        headers=headers,
    )


async def read_json_object(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = await request.json()
    except ValueError as exc:
        raise AgentSmithHttpError(
            HTTPStatus.BAD_REQUEST,
            "Bad Request",
            "Invalid JSON body",
        ) from exc
    if not isinstance(data, dict):
        raise AgentSmithHttpError(
            HTTPStatus.BAD_REQUEST,
            "Bad Request",
            "Invalid JSON body",
        )
    return data


def require_admin_token(
    authorization: str | None = Header(default=None, alias="Authorization"),
    container: HttpContainer = Depends(get_container),
) -> None:
    configured = (container.settings.admin_token or "").strip()
    if not configured:
        raise AgentSmithHttpError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "admin_auth_not_configured",
            "AGENT_SMITH_ADMIN_TOKEN is required for admin APIs.",
        )

    prefix = "Bearer "
    token = authorization[len(prefix) :].strip() if authorization and authorization.startswith(prefix) else ""
    if not token or not hmac.compare_digest(token, configured):
        raise AgentSmithHttpError(
            HTTPStatus.UNAUTHORIZED,
            "admin_unauthorized",
            "Missing or invalid admin bearer token.",
        )


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
