"""Standalone FastAPI entrypoint for the Agent Smith admin control plane."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
import re
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from agent_smith.admin.config import AdminHttpSettings
from agent_smith.app.services.identity_providers import IdentityProviderControlError
from agent_smith.bootstrap.admin_http import build_admin_http_container
from agent_smith.infra.config import load_environment
from agent_smith.transports.admin_http.audit_routes import router as audit_router
from agent_smith.transports.admin_http.auth_routes import router as auth_router
from agent_smith.transports.admin_http.provider_routes import router as provider_router
from agent_smith.transports.admin_http.security import (
    REQUEST_ID_HEADER,
    build_request_context,
)
from agent_smith.transports.shared_http import AgentSmithHttpError, error_response, json_response

REPO_ROOT = Path(__file__).resolve().parents[4]
_NON_UI_PREFIXES = ("api", "auth", "health", "docs", "redoc", "openapi.json")
_HASHED_ASSET_PATTERN = re.compile(r"(?:^|[-.])[A-Za-z0-9_-]{8,}(?=\.)")
_UI_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'; object-src 'none'; script-src 'self'; "
        "style-src 'self'; img-src 'self' data:; connect-src 'self'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app(*, container: Any | None = None) -> FastAPI:
    load_environment(REPO_ROOT / ".env")
    settings = container.settings if container is not None else AdminHttpSettings()
    ui_dist = _validated_ui_dist(settings.admin_ui_dist)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if container is not None:
            app.state.container = container
            yield
            return
        app_container = build_admin_http_container(settings)
        app.state.container = app_container
        try:
            yield
        finally:
            await app_container.close()

    docs_enabled = bool(settings.http_docs_enabled)
    app = FastAPI(
        title="Agent Smith Admin HTTP",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next: Any):
        context = build_request_context(request, settings.trusted_proxies)
        request.state.admin_request_context = context
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = context.request_id
        return response

    @app.exception_handler(AgentSmithHttpError)
    async def handle_http_error(_request: Request, exc: AgentSmithHttpError):
        return error_response(exc.status_code, exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(IdentityProviderControlError)
    async def handle_control_error(_request: Request, exc: IdentityProviderControlError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(_request: Request, _exc: SQLAlchemyError):
        return error_response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "admin_service_unavailable",
            "Admin storage is temporarily unavailable.",
        )

    @app.get("/health")
    async def health():
        return json_response({"status": "ok", "service": "agent_smith_admin_http"})

    app.include_router(auth_router)
    app.include_router(provider_router)
    app.include_router(audit_router)

    if ui_dist is not None:
        @app.get("/{ui_path:path}", include_in_schema=False)
        async def admin_ui(ui_path: str):
            normalized = ui_path.strip("/")
            first_segment = normalized.split("/", 1)[0] if normalized else ""
            if first_segment in _NON_UI_PREFIXES:
                return error_response(HTTPStatus.NOT_FOUND, "not_found", "Route not found.")

            requested = (ui_dist / normalized).resolve() if normalized else ui_dist / "index.html"
            if requested.is_relative_to(ui_dist) and requested.is_file():
                return _ui_file_response(requested, is_index=requested.name == "index.html")
            if first_segment == "assets" or "." in Path(normalized).name:
                return error_response(HTTPStatus.NOT_FOUND, "not_found", "File not found.")
            return _ui_file_response(ui_dist / "index.html", is_index=True)
    return app


def _validated_ui_dist(value: Path | None) -> Path | None:
    if value is None:
        return None
    resolved = value.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"Admin UI dist directory does not exist: {resolved}")
    if not (resolved / "index.html").is_file():
        raise ValueError(f"Admin UI dist must contain index.html: {resolved}")
    return resolved


def _ui_file_response(path: Path, *, is_index: bool) -> Response:
    headers = dict(_UI_SECURITY_HEADERS)
    if is_index:
        headers["Cache-Control"] = "no-store"
    elif _HASHED_ASSET_PATTERN.search(path.name):
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        headers["Cache-Control"] = "no-cache"
    return FileResponse(path, headers=headers)


app = create_app()


def main() -> None:
    settings = AdminHttpSettings()
    print(f"Agent Smith Admin HTTP: http://{settings.host}:{settings.port}")
    print("Expected DB schema: poetry run alembic upgrade head")
    uvicorn.run(app, host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
