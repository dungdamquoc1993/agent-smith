"""One-screen local test app for the Agent Smith runtime.

Run:
    PYTHONPATH=src poetry run python test_app/server.py
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import uuid
import warnings
from concurrent.futures import Future
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

warnings.filterwarnings(
    "ignore",
    message=r"Valid config keys have changed in V2:.*",
    category=UserWarning,
)

from pydantic import BaseModel
from sqlalchemy import select, text

from agent import AgentHarnessError, PostgresSessionRepo
from ai import bootstrap_providers, get_model, make_litellm_model, register_model
from ai.types import AssistantMessage, TextContent
from config import get_settings
from db.base import get_engine, get_session_factory
from db.models.principal import Principal, PrincipalType
from db.models.session import Session as DbSession
from resources import PostgresResourceStore, ResourceConflictError, ResourceResolver
from runtime import AgentFactory
from tools.registry import create_base_tool_registry

HOST = os.environ.get("AGENT_SMITH_TEST_APP_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENT_SMITH_TEST_APP_PORT", "8765"))
ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
STATIC_DIR = ROOT / "static"

SseQueueItem = dict[str, Any] | None


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(REPO_ROOT / ".env")

TEST_PRINCIPAL_DISPLAY_NAME = os.environ.get(
    "AGENT_SMITH_TEST_PRINCIPAL_NAME",
    "Test Principal",
)
DEFAULT_AGENT_NAME = os.environ.get("AGENT_SMITH_TEST_AGENT_NAME", "test_assistant")
OPENAI_MODEL_ID = os.environ.get("AGENT_SMITH_TEST_OPENAI_MODEL", "gpt-4o-mini")
GEMMA_MODEL_ID = os.environ.get("AGENT_SMITH_TEST_GEMMA_MODEL_ID", "gemma4-e2b")
GEMMA_UPSTREAM_MODEL = os.environ.get("AGENT_SMITH_TEST_GEMMA_UPSTREAM_MODEL", "gemma4:e2b")
GEMMA_BASE_URL = os.environ.get("AGENT_SMITH_TEST_GEMMA_BASE_URL", "http://localhost:11434/v1")
GEMMA_API_KEY = os.environ.get("AGENT_SMITH_TEST_GEMMA_API_KEY", "local")
DEFAULT_MODEL_KEY = os.environ.get("AGENT_SMITH_TEST_MODEL", "openai")


def _log_server_error(message: str, exc: BaseException | None = None) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    print(f"[{timestamp}] test_app error: {message}")
    if exc is not None:
        print(f"[{timestamp}] {exc.__class__.__name__}: {exc}")


class AsyncRuntime:
    """Single event loop for asyncpg/SQLAlchemy resources used by the test app."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="test-app-asyncio", daemon=True)
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


RUNTIME = AsyncRuntime()


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"), default=str)


def _assistant_text(message: AssistantMessage) -> str:
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent)).strip()


def _principal_payload(row: Principal) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "type": row.type.value if hasattr(row.type, "value") else row.type,
        "displayName": row.display_name,
        "status": row.status.value if hasattr(row.status, "value") else row.status,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _session_payload(row: DbSession) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "principalId": str(row.principal_id),
        "title": row.title,
        "kind": row.kind.value if hasattr(row.kind, "value") else row.kind,
        "parentSessionId": str(row.parent_session_id) if row.parent_session_id else None,
        "agentName": row.agent_name,
        "originTaskId": row.origin_task_id,
        "currentLeafId": str(row.current_leaf_id) if row.current_leaf_id else None,
        "provenance": dict(row.provenance or {}),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


async def ensure_test_principal() -> Principal:
    factory = get_session_factory()
    async with factory() as db, db.begin():
        principal = (
            await db.scalars(
                select(Principal)
                .where(Principal.display_name == TEST_PRINCIPAL_DISPLAY_NAME)
                .order_by(Principal.created_at, Principal.id)
            )
        ).first()
        if principal is None:
            principal = Principal(
                id=uuid.uuid4(),
                type=PrincipalType.human,
                display_name=TEST_PRINCIPAL_DISPLAY_NAME,
            )
            db.add(principal)
            await db.flush()
        await db.refresh(principal)
        return principal


async def list_sessions(limit: int = 25) -> list[dict[str, Any]]:
    principal = await ensure_test_principal()
    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.scalars(
                select(DbSession)
                .where(DbSession.principal_id == principal.id)
                .order_by(DbSession.updated_at.desc(), DbSession.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [_session_payload(row) for row in rows]


async def create_session(title: str | None = None) -> dict[str, Any]:
    principal = await ensure_test_principal()
    repo = PostgresSessionRepo(get_session_factory())
    session = await repo.create(
        principal_id=str(principal.id),
        title=title or "Test chat",
        provenance={"source": "test_app"},
    )
    return (await session.get_metadata()).model_dump(mode="json", by_alias=True, exclude_none=True)


async def get_session_entries(session_id: str) -> dict[str, Any]:
    session_uuid = uuid.UUID(session_id)
    principal = await ensure_test_principal()
    factory = get_session_factory()
    async with factory() as db:
        row = await db.get(DbSession, session_uuid)
        if row is None or row.principal_id != principal.id:
            raise LookupError(f"Unknown test session: {session_id}")

    repo = PostgresSessionRepo(factory)
    session = await repo.open({"id": session_id})
    metadata = await session.get_metadata()
    entries = await session.get_entries()
    return {
        "session": metadata.model_dump(mode="json", by_alias=True, exclude_none=True),
        "entries": [entry.model_dump(mode="json", by_alias=True, exclude_none=True) for entry in entries],
    }


async def list_resources() -> dict[str, Any]:
    store = PostgresResourceStore(get_session_factory())
    records = await store.list_resources()
    return {
        "resources": [
            record.model_dump(mode="json", by_alias=True, exclude_none=True)
            for record in records
        ]
    }


def _test_agent_resource() -> dict[str, Any]:
    return {
        "kind": "agent_definition",
        "name": DEFAULT_AGENT_NAME,
        "description": "Minimal assistant for the local test app.",
        "content": {
            "name": DEFAULT_AGENT_NAME,
            "description": "Minimal assistant for the local test app.",
            "systemPrompt": (
                "You are the Agent Smith local test assistant. "
                "Keep answers concise and use the user's preferred language."
            ),
            "thinkingLevel": "high",
            "model": "gpt-4o-mini",
        },
    }


async def seed_resources() -> dict[str, Any]:
    store = PostgresResourceStore(get_session_factory())
    resource = _test_agent_resource()
    existing = await store.get_resource("agent_definition", DEFAULT_AGENT_NAME)
    if existing is not None:
        updated = await store.update_resource(
            "agent_definition",
            DEFAULT_AGENT_NAME,
            {
                "description": resource["description"],
                "content": resource["content"],
            },
        )
        return {
            "status": "updated",
            "resource": updated.model_dump(mode="json", by_alias=True, exclude_none=True),
        }

    try:
        created = await store.create_resource(resource)
    except ResourceConflictError as exc:
        raise RuntimeError(
            f"Resource name is already reserved, possibly by a soft-deleted record: {DEFAULT_AGENT_NAME}"
        ) from exc

    return {
        "status": "created",
        "resource": created.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


async def bootstrap() -> dict[str, Any]:
    engine = get_engine()
    async with engine.connect() as connection:
        await connection.execute(text("select 1"))
    principal = await ensure_test_principal()
    return {
        "database": {"ok": True, "url": get_settings().database_url},
        "principal": _principal_payload(principal),
        "sessions": await list_sessions(),
        "resources": (await list_resources())["resources"],
        "defaults": {"agentName": DEFAULT_AGENT_NAME, "modelKey": _default_model_key()},
        "models": _model_choices(),
    }


def _openai_model():
    return get_model("openai", OPENAI_MODEL_ID) or make_litellm_model(
        provider="openai",
        model_id=OPENAI_MODEL_ID,
    )


def _gemma_model():
    return get_model("local", GEMMA_MODEL_ID) or make_litellm_model(
        provider="local",
        model_id=GEMMA_MODEL_ID,
        name="Gemma 4 E2B local",
        litellm_model=f"openai/{GEMMA_UPSTREAM_MODEL}",
        base_url=GEMMA_BASE_URL,
        reasoning=True,
        input=["text", "image"],
        context_window=128_000,
        max_tokens=4096,
        provider_options={
            "api_key": GEMMA_API_KEY,
            "ollama_native": True,
            "ollama_think": True,
        },
    )


def _register_test_models() -> None:
    register_model(_gemma_model())


def _model_choices() -> list[dict[str, str]]:
    return [
        {
            "key": "openai",
            "label": f"OpenAI · {OPENAI_MODEL_ID}",
            "provider": "openai",
            "modelId": OPENAI_MODEL_ID,
        },
        {
            "key": "gemma",
            "label": f"Gemma local · {GEMMA_UPSTREAM_MODEL}",
            "provider": "local",
            "modelId": GEMMA_MODEL_ID,
            "baseUrl": GEMMA_BASE_URL,
        },
    ]


def _default_model_key() -> str:
    keys = {choice["key"] for choice in _model_choices()}
    return DEFAULT_MODEL_KEY if DEFAULT_MODEL_KEY in keys else "openai"


def _selected_model(model_key: str | None):
    key = (model_key or _default_model_key()).strip()
    if key == "openai":
        return _openai_model()
    if key == "gemma":
        return _gemma_model()
    raise ValueError(f"Unknown model selection: {key}")


async def _open_or_create_session(session_id: str | None) -> Any:
    principal = await ensure_test_principal()
    factory = get_session_factory()
    repo = PostgresSessionRepo(factory)
    if session_id:
        session_uuid = uuid.UUID(session_id)
        async with factory() as db:
            row = await db.get(DbSession, session_uuid)
            if row is None or row.principal_id != principal.id:
                raise LookupError(f"Unknown test session: {session_id}")
        return await repo.open({"id": session_id})
    return await repo.create(
        principal_id=str(principal.id),
        title="Test chat",
        provenance={"source": "test_app"},
    )


async def run_prompt_stream(payload: dict[str, Any], out: "queue.Queue[SseQueueItem]") -> None:
    try:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        agent_name = str(payload.get("agentName") or DEFAULT_AGENT_NAME).strip()
        session_id = payload.get("sessionId")
        if session_id is not None:
            session_id = str(session_id)
        selected_model = _selected_model(
            str(payload.get("modelKey")) if payload.get("modelKey") is not None else None
        )

        store = PostgresResourceStore(get_session_factory())
        resolver = ResourceResolver([store])
        tool_registry = create_base_tool_registry(
            resources_store=store,
            resources_resolver=resolver,
            sleep_max_seconds=5,
        )
        factory = AgentFactory(
            resource_resolver=resolver,
            tool_registry=tool_registry,
            default_model=selected_model,
            model_resolver=lambda _definition: selected_model,
            default_permission_mode=get_settings().default_permission_mode,
        )
        session = await _open_or_create_session(session_id)
        metadata = await session.get_metadata()
        out.put({"event": "session", "data": metadata})

        harness = await factory.create_harness(agent_name, session=session)

        async def emit(event: Any) -> None:
            out.put({"event": "harness", "data": event})

        unsubscribe = harness.subscribe(emit)
        try:
            response = await harness.prompt(prompt)
        finally:
            unsubscribe()

        if response.error_message:
            _log_server_error(f"Model error for agent '{agent_name}': {response.error_message}")

        out.put(
            {
                "event": "done",
                "data": {
                    "message": response,
                    "text": _assistant_text(response),
                    "session": await session.get_metadata(),
                    "entries": await session.get_entries(),
                },
            }
        )
    except AgentHarnessError as exc:
        _log_server_error(f"Agent harness error [{exc.code}]", exc)
        out.put(
            {
                "event": "error",
                "data": {"code": exc.code, "message": "Prompt failed. Check the server log for details."},
            }
        )
    except Exception as exc:  # pragma: no cover - surfaced in local test UI
        _log_server_error("Unexpected prompt error", exc)
        out.put(
            {
                "event": "error",
                "data": {
                    "code": exc.__class__.__name__,
                    "message": "Prompt failed. Check the server log for details.",
                },
            }
        )
    finally:
        out.put(None)


class TestAppHandler(BaseHTTPRequestHandler):
    server_version = "AgentSmithTestApp/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                return self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            if path == "/api/bootstrap":
                return self._send_json(RUNTIME.run(bootstrap()))
            if path == "/api/sessions":
                return self._send_json({"sessions": RUNTIME.run(list_sessions())})
            if path == "/api/resources":
                return self._send_json(RUNTIME.run(list_resources()))
            if path.startswith("/api/sessions/") and path.endswith("/entries"):
                session_id = path.removeprefix("/api/sessions/").removesuffix("/entries").strip("/")
                return self._send_json(RUNTIME.run(get_session_entries(session_id)))
            return self._send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            body = self._read_json()
            if path == "/api/sessions":
                return self._send_json(RUNTIME.run(create_session(body.get("title"))), status=HTTPStatus.CREATED)
            if path == "/api/resources/seed":
                return self._send_json(RUNTIME.run(seed_resources()))
            if path == "/api/prompt/stream":
                return self._send_prompt_stream(body)
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
        content = _json_dumps(data).encode("utf-8")
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

        events: "queue.Queue[SseQueueItem]" = queue.Queue()
        future = RUNTIME.submit(run_prompt_stream(body, events))

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
                event_name = str(item.get("event") or "message")
                data = _json_dumps(item.get("data"))
                chunk = f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            future.cancel()
        finally:
            if future.done() and not future.cancelled():
                future.result()
            self.close_connection = True


def main() -> None:
    bootstrap_providers()
    _register_test_models()
    server = ThreadingHTTPServer((HOST, PORT), TestAppHandler)
    print(f"Agent Smith test app: http://{HOST}:{PORT}")
    print("Expected DB schema: poetry run alembic upgrade head")
    print(f"OPENAI_API_KEY loaded: {'yes' if os.environ.get('OPENAI_API_KEY') else 'no'}")
    print(f"Gemma local endpoint: {GEMMA_BASE_URL} model={GEMMA_UPSTREAM_MODEL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping test app...")
    finally:
        server.server_close()
        RUNTIME.stop()


if __name__ == "__main__":
    main()
