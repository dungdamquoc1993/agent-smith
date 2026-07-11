"""Runtime HTTP routes for local Agent Smith testing."""

from __future__ import annotations

import queue
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

from pydantic import ValidationError

from agent_smith.app.auth import AppAssertionError
from agent_smith.app.context import ContextResolutionError
from agent_smith.app.container import AppContainer
from agent_smith.transports.http.common import SseQueueItem, send_error, send_json, send_sse_stream

RUNTIME_ROUTES = [
    "/api/bootstrap",
    "/api/sessions",
    "/api/resources",
    "/api/resources/seed",
    "/api/prompt/stream",
    "/api/agent/invoke/stream",
]


def handle_runtime_get(
    *,
    handler: BaseHTTPRequestHandler,
    path: str,
    container: AppContainer,
    runtime: Any,
) -> bool:
    if path == "/api/bootstrap":
        send_json(handler, runtime.run(container.bootstrap()))
        return True
    if path == "/api/sessions":
        send_json(handler, {"sessions": runtime.run(container.sessions.list_sessions())})
        return True
    if path == "/api/resources":
        send_json(handler, runtime.run(container.resources.list_resources()))
        return True
    if path.startswith("/api/sessions/") and path.endswith("/entries"):
        session_id = path.removeprefix("/api/sessions/").removesuffix("/entries").strip("/")
        send_json(handler, runtime.run(container.sessions.get_session_entries(session_id)))
        return True
    return False


def handle_runtime_post(
    *,
    handler: BaseHTTPRequestHandler,
    path: str,
    body: dict[str, Any],
    container: AppContainer,
    runtime: Any,
) -> bool:
    if path == "/api/sessions":
        send_json(
            handler,
            runtime.run(container.sessions.create_session(body.get("title"))),
            status=HTTPStatus.CREATED,
        )
        return True
    if path == "/api/resources/seed":
        send_json(handler, runtime.run(container.resources.seed_default_agent()))
        return True
    if path == "/api/prompt/stream":
        _send_prompt_stream(handler=handler, body=body, container=container, runtime=runtime)
        return True
    if path == "/api/agent/invoke/stream":
        _send_agent_invoke_stream(handler=handler, body=body, container=container, runtime=runtime)
        return True
    return False


def _send_prompt_stream(
    *,
    handler: BaseHTTPRequestHandler,
    body: dict[str, Any],
    container: AppContainer,
    runtime: Any,
) -> None:
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return send_error(handler, HTTPStatus.BAD_REQUEST, "prompt is required")

    events: queue.Queue[SseQueueItem] = queue.Queue()

    async def run_prompt() -> None:
        try:
            await container.agent_runs.run_prompt_stream(
                body,
                lambda event, data: events.put({"event": event, "data": data}),
            )
        finally:
            events.put(None)

    send_sse_stream(handler, future=runtime.submit(run_prompt()), events=events)


def _send_agent_invoke_stream(
    *,
    handler: BaseHTTPRequestHandler,
    body: dict[str, Any],
    container: AppContainer,
    runtime: Any,
) -> None:
    try:
        prepared = runtime.run(
            container.agent_runs.prepare_invocation(
                provider_api_key=handler.headers.get("x-agent-smith-provider-key"),
                authorization=handler.headers.get("authorization"),
                body=body,
            )
        )
    except AppAssertionError as exc:
        return send_json(
            handler,
            {"error": {"code": exc.code, "message": exc.message}},
            status=HTTPStatus.UNAUTHORIZED,
        )
    except ValidationError as exc:
        return send_json(
            handler,
            {"error": {"code": "invalid_invocation", "message": str(exc)}},
            status=HTTPStatus.BAD_REQUEST,
        )
    except ContextResolutionError as exc:
        return send_json(
            handler,
            {"error": {"code": "invalid_context", "message": str(exc)}},
            status=HTTPStatus.BAD_REQUEST,
        )
    except LookupError as exc:
        return send_json(
            handler,
            {"error": {"code": "unknown_session", "message": str(exc)}},
            status=HTTPStatus.NOT_FOUND,
        )

    events: queue.Queue[SseQueueItem] = queue.Queue()

    async def run_invocation() -> None:
        try:
            await container.agent_runs.run_prepared_invocation_stream(
                prepared,
                lambda event, data: events.put({"event": event, "data": data}),
            )
        finally:
            events.put(None)

    send_sse_stream(handler, future=runtime.submit(run_invocation()), events=events)
