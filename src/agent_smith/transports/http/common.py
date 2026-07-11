"""Shared helpers for the stdlib HTTP transport."""

from __future__ import annotations

import hmac
import json
import queue
from concurrent.futures import Future
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from agent_smith.app.container import AppContainer
from agent_smith.transports.http.sse import json_dumps, sse_chunk

SseQueueItem = dict[str, Any] | None


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("content-length") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise json.JSONDecodeError("Expected JSON object", raw, 0)
    return data


def serve_file(handler: BaseHTTPRequestHandler, path: Path, content_type: str) -> None:
    if not path.is_file():
        return send_error(handler, HTTPStatus.NOT_FOUND, "File not found")
    content = path.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler.send_header("content-type", content_type)
    handler.send_header("content-length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def send_json(
    handler: BaseHTTPRequestHandler,
    data: Any,
    *,
    status: HTTPStatus | int = HTTPStatus.OK,
) -> None:
    content = json_dumps(data).encode("utf-8")
    handler.send_response(int(status))
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def send_error(
    handler: BaseHTTPRequestHandler,
    status: HTTPStatus | int,
    message: str,
    *,
    code: str | None = None,
) -> None:
    status_code = HTTPStatus(int(status))
    send_json(
        handler,
        {"error": {"code": code or status_code.phrase, "message": message}},
        status=status_code,
    )


def send_sse_stream(
    handler: BaseHTTPRequestHandler,
    *,
    future: Future,
    events: queue.Queue[SseQueueItem],
) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header("content-type", "text/event-stream; charset=utf-8")
    handler.send_header("cache-control", "no-cache")
    handler.send_header("connection", "close")
    handler.end_headers()

    try:
        while True:
            item = events.get()
            if item is None:
                break
            handler.wfile.write(sse_chunk(str(item.get("event") or "message"), item.get("data")))
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        future.cancel()
    finally:
        if future.done() and not future.cancelled():
            future.result()
        handler.close_connection = True


def require_admin_auth(handler: BaseHTTPRequestHandler, container: AppContainer) -> bool:
    configured = (container.settings.admin_token or "").strip()
    if not configured:
        send_error(
            handler,
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "AGENT_SMITH_ADMIN_TOKEN is required for admin APIs.",
            code="admin_auth_not_configured",
        )
        return False

    authorization = handler.headers.get("authorization") or ""
    prefix = "Bearer "
    token = authorization[len(prefix) :].strip() if authorization.startswith(prefix) else ""
    if not token or not hmac.compare_digest(token, configured):
        send_error(
            handler,
            HTTPStatus.UNAUTHORIZED,
            "Missing or invalid admin bearer token.",
            code="admin_unauthorized",
        )
        return False
    return True
