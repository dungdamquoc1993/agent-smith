"""FastAPI HTTP/SSE entrypoint for Agent Smith."""

from __future__ import annotations

import os
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

from agent_smith.app.container import AppContainer, load_dotenv
from agent_smith.app.services.identity_providers import IdentityProviderManagementError
from agent_smith.infra.config import get_settings
from agent_smith.transports.http.admin_identity_provider_routes import (
    ADMIN_IDENTITY_PROVIDER_ROUTES,
    router as admin_identity_provider_router,
)
from agent_smith.transports.http.common import AgentSmithHttpError, error_response, json_response
from agent_smith.transports.http.runtime_routes import RUNTIME_ROUTES, router as runtime_router

warnings.filterwarnings(
    "ignore",
    message=r"Valid config keys have changed in V2:.*",
    category=UserWarning,
)

HOST = os.environ.get("AGENT_SMITH_TEST_APP_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENT_SMITH_TEST_APP_PORT", "8765"))
REPO_ROOT = Path(__file__).resolve().parents[4]


def create_app(
    *,
    container: Any | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    load_dotenv(REPO_ROOT / ".env")
    settings = container.settings if container is not None else get_settings()
    docs_enabled = bool(getattr(settings, "http_docs_enabled", True))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if container is not None:
            app.state.container = container
            yield
            return

        app_container = AppContainer()
        app_container.bootstrap_providers()
        app.state.container = app_container
        yield

    app = FastAPI(
        title="Agent Smith HTTP",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.include_router(runtime_router)
    app.include_router(admin_identity_provider_router)

    @app.exception_handler(AgentSmithHttpError)
    async def handle_http_error(_request: Any, exc: AgentSmithHttpError):
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(IdentityProviderManagementError)
    async def handle_management_error(_request: Any, exc: IdentityProviderManagementError):
        return error_response(exc.status, exc.code, exc.message)

    @app.get("/")
    @app.get("/index.html")
    async def root():
        if static_dir is not None:
            index = static_dir / "index.html"
            if index.is_file():
                return FileResponse(index, media_type="text/html; charset=utf-8")
            return error_response(HTTPStatus.NOT_FOUND, "Not Found", "File not found")
        return json_response(
            {
                "service": "agent_smith_http",
                "routes": [
                    *RUNTIME_ROUTES,
                    *ADMIN_IDENTITY_PROVIDER_ROUTES,
                ],
            }
        )

    return app


app = create_app()


def main() -> None:
    print(f"Agent Smith HTTP adapter: http://{HOST}:{PORT}")
    print("Expected DB schema: poetry run alembic upgrade head")
    print(f"OPENAI_API_KEY loaded: {'yes' if os.environ.get('OPENAI_API_KEY') else 'no'}")
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
