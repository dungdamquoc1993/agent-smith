"""Runtime FastAPI routes for local Agent Smith testing."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import ValidationError

from agent_smith.app.auth import AppAssertionError
from agent_smith.app.context import ContextResolutionError
from agent_smith.app.services.attachments import AttachmentError
from agent_smith.app.container import AppContainer
from agent_smith.transports.http.common import (
    AgentSmithHttpError,
    get_container,
    json_response,
    read_json_object,
    sse_response,
)

RUNTIME_ROUTES = [
    "/api/bootstrap",
    "/api/models",
    "/api/sessions",
    "/api/resources",
    "/api/resources/seed",
    "/api/prompt/stream",
    "/api/agent/invoke/stream",
]

router = APIRouter()


@router.get("/api/bootstrap")
async def bootstrap(container: AppContainer = Depends(get_container)):
    return json_response(await container.bootstrap())


@router.get("/api/models")
async def models(container: AppContainer = Depends(get_container)):
    return json_response(container.model_catalog())


@router.get("/api/sessions")
async def list_sessions(container: AppContainer = Depends(get_container)):
    return json_response({"sessions": await container.sessions.list_sessions()})


@router.post("/api/sessions", status_code=int(HTTPStatus.CREATED))
async def create_session(
    request: Request,
    container: AppContainer = Depends(get_container),
):
    body = await read_json_object(request)
    return json_response(
        await container.sessions.create_session(body.get("title")),
        status_code=HTTPStatus.CREATED,
    )


@router.get("/api/sessions/{session_id}/entries")
async def get_session_entries(
    session_id: str,
    container: AppContainer = Depends(get_container),
):
    try:
        return json_response(await container.sessions.get_session_entries(session_id))
    except LookupError as exc:
        raise AgentSmithHttpError(HTTPStatus.NOT_FOUND, "unknown_session", str(exc)) from exc


@router.get("/api/resources")
async def list_resources(container: AppContainer = Depends(get_container)):
    return json_response(await container.resources.list_resources())


@router.post("/api/resources/seed")
async def seed_default_resource(container: AppContainer = Depends(get_container)):
    return json_response(await container.resources.seed_default_agent())


@router.post("/api/prompt/stream")
async def prompt_stream(
    request: Request,
    container: AppContainer = Depends(get_container),
):
    body = await read_json_object(request)
    try:
        prepared = await container.agent_runs.prepare_prompt(body)
    except AttachmentError as exc:
        raise AgentSmithHttpError(exc.status, exc.code, exc.message) from exc
    except LookupError as exc:
        raise AgentSmithHttpError(HTTPStatus.NOT_FOUND, "unknown_session", str(exc)) from exc
    except ValueError as exc:
        raise AgentSmithHttpError(HTTPStatus.BAD_REQUEST, "invalid_prompt", str(exc)) from exc

    async def run(emit: Any) -> None:
        await container.agent_runs.run_prepared_prompt_stream(prepared, emit)

    return sse_response(run)


@router.post("/api/agent/invoke/stream")
async def agent_invoke_stream(
    request: Request,
    provider_api_key: str | None = Header(default=None, alias="X-Agent-Smith-Provider-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    container: AppContainer = Depends(get_container),
):
    body = await read_json_object(request)
    try:
        prepared = await container.agent_runs.prepare_invocation(
            provider_api_key=provider_api_key,
            authorization=authorization,
            body=body,
        )
    except AppAssertionError as exc:
        raise AgentSmithHttpError(HTTPStatus.UNAUTHORIZED, exc.code, exc.message) from exc
    except ValidationError as exc:
        raise AgentSmithHttpError(HTTPStatus.BAD_REQUEST, "invalid_invocation", str(exc)) from exc
    except ContextResolutionError as exc:
        raise AgentSmithHttpError(HTTPStatus.BAD_REQUEST, "invalid_context", str(exc)) from exc
    except LookupError as exc:
        raise AgentSmithHttpError(HTTPStatus.NOT_FOUND, "unknown_session", str(exc)) from exc
    except AttachmentError as exc:
        raise AgentSmithHttpError(exc.status, exc.code, exc.message) from exc
    except ValueError as exc:
        raise AgentSmithHttpError(HTTPStatus.BAD_REQUEST, "invalid_invocation", str(exc)) from exc

    async def run(emit: Any) -> None:
        await container.agent_runs.run_prepared_invocation_stream(prepared, emit)

    return sse_response(run)
