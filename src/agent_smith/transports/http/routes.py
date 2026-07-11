"""Thin HTTP transport for local Agent Smith testing."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from concurrent.futures import Future
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from agent_smith.app.auth import AppAssertionError
from agent_smith.app.context import ContextResolutionError
from agent_smith.app.container import AppContainer
from agent_smith.transports.http.sse import json_dumps, sse_chunk

SseQueueItem = dict[str, Any] | None


class AsyncRuntime:
    """Single event loop for asyncpg/SQLAlchemy resources used by the HTTP adapter."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="agent-smith-http-asyncio", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro: Any) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Any) -> Any:
        return self.submit(coro).result()

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


def create_handler(
    *,
    container: AppContainer,
    runtime: AsyncRuntime,
    static_dir: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    class AgentSmithHttpHandler(BaseHTTPRequestHandler):
        server_version = "AgentSmithHTTP/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def do_GET(self) -> None:
            try:
                path = urlparse(self.path).path
                if path in {"/", "/index.html"}:
                    if static_dir is not None:
                        return self._serve_file(static_dir / "index.html", "text/html; charset=utf-8")
                    return self._send_json(
                        {
                            "service": "agent_smith_http",
                            "routes": [
                                "/api/bootstrap",
                                "/api/sessions",
                                "/api/resources",
                                "/api/resources/seed",
                                "/api/prompt/stream",
                                "/api/agent/invoke/stream",
                            ],
                        }
                    )
                if path == "/api/bootstrap":
                    return self._send_json(runtime.run(container.bootstrap()))
                if path == "/api/sessions":
                    return self._send_json({"sessions": runtime.run(container.sessions.list_sessions())})
                if path == "/api/resources":
                    return self._send_json(runtime.run(container.resources.list_resources()))
                if path.startswith("/api/sessions/") and path.endswith("/entries"):
                    session_id = path.removeprefix("/api/sessions/").removesuffix("/entries").strip("/")
                    return self._send_json(
                        runtime.run(container.sessions.get_session_entries(session_id))
                    )
                return self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_POST(self) -> None:
            try:
                path = urlparse(self.path).path
                body = self._read_json()
                if path == "/api/sessions":
                    return self._send_json(
                        runtime.run(container.sessions.create_session(body.get("title"))),
                        status=HTTPStatus.CREATED,
                    )
                if path == "/api/resources/seed":
                    return self._send_json(runtime.run(container.resources.seed_default_agent()))
                if path == "/api/prompt/stream":
                    return self._send_prompt_stream(body)
                if path == "/api/agent/invoke/stream":
                    return self._send_agent_invoke_stream(body)
                return self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            except json.JSONDecodeError:
                return self._send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
            except Exception as exc:
                return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise json.JSONDecodeError("Expected JSON object", raw, 0)
            return data

        def _serve_file(self, path: Path, content_type: str) -> None:
            if not path.is_file():
                return self._send_error(HTTPStatus.NOT_FOUND, "File not found")
            content = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, data: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            content = json_dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": {"code": status.phrase, "message": message}}, status=status)

        def _send_prompt_stream(self, body: dict[str, Any]) -> None:
            prompt = str(body.get("prompt") or "").strip()
            if not prompt:
                return self._send_error(HTTPStatus.BAD_REQUEST, "prompt is required")

            events: queue.Queue[SseQueueItem] = queue.Queue()

            async def run_prompt() -> None:
                try:
                    await container.agent_runs.run_prompt_stream(
                        body,
                        lambda event, data: events.put({"event": event, "data": data}),
                    )
                finally:
                    events.put(None)

            future = runtime.submit(run_prompt())

            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()

            try:
                while True:
                    item = events.get()
                    if item is None:
                        break
                    self.wfile.write(sse_chunk(str(item.get("event") or "message"), item.get("data")))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                future.cancel()
            finally:
                if future.done() and not future.cancelled():
                    future.result()
                self.close_connection = True

        def _send_agent_invoke_stream(self, body: dict[str, Any]) -> None:
            try:
                prepared = runtime.run(
                    container.agent_runs.prepare_invocation(
                        provider_api_key=self.headers.get("x-agent-smith-provider-key"),
                        authorization=self.headers.get("authorization"),
                        body=body,
                    )
                )
            except AppAssertionError as exc:
                return self._send_json(
                    {"error": {"code": exc.code, "message": exc.message}},
                    status=HTTPStatus.UNAUTHORIZED,
                )
            except ValidationError as exc:
                return self._send_json(
                    {"error": {"code": "invalid_invocation", "message": str(exc)}},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except ContextResolutionError as exc:
                return self._send_json(
                    {"error": {"code": "invalid_context", "message": str(exc)}},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except LookupError as exc:
                return self._send_json(
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

            future = runtime.submit(run_invocation())

            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/event-stream; charset=utf-8")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()

            try:
                while True:
                    item = events.get()
                    if item is None:
                        break
                    self.wfile.write(sse_chunk(str(item.get("event") or "message"), item.get("data")))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                future.cancel()
            finally:
                if future.done() and not future.cancelled():
                    future.result()
                self.close_connection = True

    return AgentSmithHttpHandler
