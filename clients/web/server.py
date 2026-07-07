"""Local fake HRIS parent-app client for Agent Smith development."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlparse


CLIENT_HOST = os.environ.get("HRIS_SANDBOX_CLIENT_HOST", "127.0.0.1")
CLIENT_PORT = int(os.environ.get("HRIS_SANDBOX_CLIENT_PORT", "5173"))
SMITH_URL = os.environ.get("AGENT_SMITH_URL", "http://127.0.0.1:8765").rstrip("/")
ASSERTION_ISSUER = os.environ.get("HRIS_SANDBOX_ASSERTION_ISSUER", "hris-sandbox")
ASSERTION_AUDIENCE = os.environ.get("AGENT_SMITH_ASSERTION_AUDIENCE", "agent-smith")
ASSERTION_KEY_ID = os.environ.get("HRIS_SANDBOX_ASSERTION_KEY_ID", "dev-v1")
ASSERTION_SECRET = os.environ.get("HRIS_SANDBOX_ASSERTION_SECRET", "dev-secret-change-me")
DEFAULT_AGENT_NAME = os.environ.get("HRIS_SANDBOX_AGENT_NAME", "test_assistant")
STATIC_DIR = Path(__file__).resolve().parent


class HrisSandboxHandler(BaseHTTPRequestHandler):
    server_version = "HrisSandboxClient/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html", "/app.js", "/styles.css"}:
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            return self._serve_file(STATIC_DIR / "index.html")
        if path in {"/app.js", "/styles.css"}:
            return self._serve_file(STATIC_DIR / path.lstrip("/"))
        return self._send_json(
            {"error": {"code": "not_found", "message": "Not found."}},
            status=HTTPStatus.NOT_FOUND,
        )

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/oneai/chat/stream":
            return self._send_json(
                {"error": {"code": "not_found", "message": "Not found."}},
                status=HTTPStatus.NOT_FOUND,
            )
        try:
            payload = self._read_json()
            relay_request = build_smith_request(payload)
        except ValueError as exc:
            return self._send_json(
                {"error": {"code": "invalid_request", "message": str(exc)}},
                status=HTTPStatus.BAD_REQUEST,
            )
        self._relay_smith_stream(relay_request)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or "0")
        if length <= 0:
            raise ValueError("JSON body is required.")
        raw = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON body.") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object.")
        return data

    def _serve_file(self, path: Path) -> None:
        if not path.is_file() or path.parent != STATIC_DIR:
            return self._send_json(
                {"error": {"code": "not_found", "message": "File not found."}},
                status=HTTPStatus.NOT_FOUND,
            )
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or path.suffix in {".js", ".css"}:
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, data: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _relay_smith_stream(self, relay_request: request.Request) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        try:
            with request.urlopen(relay_request, timeout=600) as response:
                while True:
                    chunk = response.readline()
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._write_sse(
                "run.failed",
                {
                    "event": "run.failed",
                    "data": {
                        "code": f"smith_http_{exc.code}",
                        "message": extract_error_message(detail) or "Agent Smith rejected the request.",
                    },
                },
            )
        except OSError as exc:
            self._write_sse(
                "run.failed",
                {
                    "event": "run.failed",
                    "data": {
                        "code": "smith_unreachable",
                        "message": f"Cannot reach Agent Smith at {SMITH_URL}: {exc}",
                    },
                },
            )
        finally:
            self.close_connection = True

    def _write_sse(self, event: str, data: Any) -> None:
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"data: {encoded}\n\n".encode("utf-8"))
        self.wfile.flush()


def build_smith_request(payload: dict[str, Any]) -> request.Request:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required.")
    user = payload.get("user")
    if not isinstance(user, dict):
        raise ValueError("user is required.")
    subject = str(user.get("id") or user.get("employeeId") or "").strip()
    if not subject:
        raise ValueError("user.id or user.employeeId is required.")
    display_name = str(user.get("displayName") or subject).strip()
    email = str(user.get("email") or "").strip() or None
    department = str(user.get("department") or "").strip() or None
    roles = user.get("roles")
    if not isinstance(roles, list):
        roles = []
    clean_roles = [str(role) for role in roles if str(role).strip()]

    smith_body = {
        "payload": {
            "prompt": prompt,
            "agentName": str(payload.get("agentName") or DEFAULT_AGENT_NAME),
            "modelKey": str(payload.get("modelKey") or "openai"),
        },
        "session": {
            "smithSessionId": payload.get("smithSessionId") or None,
            "externalSessionId": payload.get("externalSessionId") or None,
        },
        "surface": {
            "app": "hris-sandbox",
            "route": "/oneai",
            "origin": f"http://{CLIENT_HOST}:{CLIENT_PORT}",
            "locale": payload.get("locale") or "vi-VN",
            "timezone": payload.get("timezone") or "Asia/Ho_Chi_Minh",
            "userAgent": payload.get("userAgent") or "hris-sandbox-client",
        },
        "metadata": {
            "hris": {
                "employeeId": user.get("employeeId"),
                "managerId": user.get("managerId"),
                "title": user.get("title"),
                "location": user.get("location"),
            }
        },
        "correlationId": str(uuid.uuid4()),
    }
    token = sign_assertion(
        subject=subject,
        display_name=display_name,
        email=email,
        roles=clean_roles,
        department=department,
        employee_id=str(user.get("employeeId") or subject),
    )
    body = json.dumps(smith_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return request.Request(
        f"{SMITH_URL}/api/agent/invoke/stream",
        data=body,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "text/event-stream",
        },
        method="POST",
    )


def sign_assertion(
    *,
    subject: str,
    display_name: str,
    email: str | None,
    roles: list[str],
    department: str | None,
    employee_id: str,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": ASSERTION_KEY_ID}
    claims = {
        "iss": ASSERTION_ISSUER,
        "aud": ASSERTION_AUDIENCE,
        "sub": subject,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 300,
        "actor": {
            "provider": ASSERTION_ISSUER,
            "subject": subject,
            "displayName": display_name,
            "email": email,
            "roles": roles,
            "department": department,
            "upstreamAuth": {
                "provider": "hris",
                "subject": employee_id,
                "assurance": "asserted_by_hris_sandbox",
                "method": "fake_sign_in",
            },
        },
    }
    signing_input = b".".join([b64url_json(header), b64url_json(claims)])
    signature = hmac.new(ASSERTION_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{signing_input.decode('ascii')}.{b64url(signature)}"


def b64url_json(data: dict[str, Any]) -> bytes:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b64url(raw).encode("ascii")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def extract_error_message(detail: str) -> str | None:
    try:
        parsed = json.loads(detail)
    except json.JSONDecodeError:
        return detail.strip() or None
    error_obj = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error_obj, dict):
        message = error_obj.get("message")
        if isinstance(message, str) and message:
            return message
    return detail.strip() or None


def main() -> None:
    server = ThreadingHTTPServer((CLIENT_HOST, CLIENT_PORT), HrisSandboxHandler)
    print(f"Fake HRIS client: http://{CLIENT_HOST}:{CLIENT_PORT}")
    print(f"Agent Smith upstream: {SMITH_URL}")
    print(f"Assertion issuer: {ASSERTION_ISSUER} kid={ASSERTION_KEY_ID}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping fake HRIS client...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
