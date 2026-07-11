"""Thin HTTP transport for local Agent Smith testing."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_smith.app.container import AppContainer
from agent_smith.transports.http.admin_identity_provider_routes import (
    ADMIN_IDENTITY_PROVIDER_ROUTES,
    handle_admin_get,
    handle_admin_patch,
    handle_admin_post,
)
from agent_smith.transports.http.common import read_json, send_error, send_json, serve_file
from agent_smith.transports.http.runtime_routes import (
    RUNTIME_ROUTES,
    handle_runtime_get,
    handle_runtime_post,
)


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
                    return self._send_root()
                if handle_admin_get(
                    handler=self,
                    path=path,
                    container=container,
                    runtime=runtime,
                ):
                    return
                if handle_runtime_get(
                    handler=self,
                    path=path,
                    container=container,
                    runtime=runtime,
                ):
                    return
                return send_error(self, HTTPStatus.NOT_FOUND, "Not found")
            except Exception as exc:
                return send_error(self, HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_POST(self) -> None:
            try:
                path = urlparse(self.path).path
                body = read_json(self)
                if handle_admin_post(
                    handler=self,
                    path=path,
                    body=body,
                    container=container,
                    runtime=runtime,
                ):
                    return
                if handle_runtime_post(
                    handler=self,
                    path=path,
                    body=body,
                    container=container,
                    runtime=runtime,
                ):
                    return
                return send_error(self, HTTPStatus.NOT_FOUND, "Not found")
            except json.JSONDecodeError:
                return send_error(self, HTTPStatus.BAD_REQUEST, "Invalid JSON body")
            except Exception as exc:
                return send_error(self, HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_PATCH(self) -> None:
            try:
                path = urlparse(self.path).path
                body = read_json(self)
                if handle_admin_patch(
                    handler=self,
                    path=path,
                    body=body,
                    container=container,
                    runtime=runtime,
                ):
                    return
                return send_error(self, HTTPStatus.NOT_FOUND, "Not found")
            except json.JSONDecodeError:
                return send_error(self, HTTPStatus.BAD_REQUEST, "Invalid JSON body")
            except Exception as exc:
                return send_error(self, HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def _send_root(self) -> None:
            if static_dir is not None:
                return serve_file(self, static_dir / "index.html", "text/html; charset=utf-8")
            return send_json(
                self,
                {
                    "service": "agent_smith_http",
                    "routes": [
                        *RUNTIME_ROUTES,
                        *ADMIN_IDENTITY_PROVIDER_ROUTES,
                    ],
                },
            )

    return AgentSmithHttpHandler
