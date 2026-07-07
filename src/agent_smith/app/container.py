"""Application composition root."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import text

from agent_smith.app.auth import AppAssertionVerifier, parse_trusted_apps
from agent_smith.app.services.agent_runs import AgentRunService
from agent_smith.app.services.identity import PrincipalIdentityService
from agent_smith.app.services.resources import ResourceService
from agent_smith.app.services.sessions import SessionService, principal_payload
from agent_smith.app.services.tasks import TaskService
from agent_smith.core.llm import bootstrap_providers
from agent_smith.core.tasks import MemoryTaskRuntime
from agent_smith.infra.config import get_settings
from agent_smith.infra.db.base import get_engine, get_session_factory


DEFAULT_PRINCIPAL_DISPLAY_NAME = "Test Principal"
DEFAULT_AGENT_NAME = "test_assistant"
DEFAULT_OPENAI_MODEL_ID = "gpt-4o-mini"
DEFAULT_GEMMA_MODEL_ID = "gemma4-e2b"
DEFAULT_GEMMA_UPSTREAM_MODEL = "gemma4:e2b"
DEFAULT_GEMMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_GEMMA_API_KEY = "local"
DEFAULT_MODEL_KEY = "openai"


class AppContainer:
    def __init__(self) -> None:
        settings = get_settings()
        session_factory = get_session_factory()
        self.settings = settings
        self.sessions = SessionService(
            session_factory,
            principal_display_name=os.environ.get(
                "AGENT_SMITH_TEST_PRINCIPAL_NAME",
                DEFAULT_PRINCIPAL_DISPLAY_NAME,
            ),
        )
        self.identities = PrincipalIdentityService(session_factory)
        self.resources = ResourceService(
            session_factory,
            default_agent_name=os.environ.get("AGENT_SMITH_TEST_AGENT_NAME", DEFAULT_AGENT_NAME),
        )
        self.tasks = TaskService(MemoryTaskRuntime())
        self.agent_runs = AgentRunService(
            session_service=self.sessions,
            resource_service=self.resources,
            default_permission_mode=settings.default_permission_mode,
            openai_model_id=os.environ.get("AGENT_SMITH_TEST_OPENAI_MODEL", DEFAULT_OPENAI_MODEL_ID),
            gemma_model_id=os.environ.get("AGENT_SMITH_TEST_GEMMA_MODEL_ID", DEFAULT_GEMMA_MODEL_ID),
            gemma_upstream_model=os.environ.get(
                "AGENT_SMITH_TEST_GEMMA_UPSTREAM_MODEL",
                DEFAULT_GEMMA_UPSTREAM_MODEL,
            ),
            gemma_base_url=os.environ.get("AGENT_SMITH_TEST_GEMMA_BASE_URL", DEFAULT_GEMMA_BASE_URL),
            gemma_api_key=os.environ.get("AGENT_SMITH_TEST_GEMMA_API_KEY", DEFAULT_GEMMA_API_KEY),
            default_model_key=os.environ.get("AGENT_SMITH_TEST_MODEL", DEFAULT_MODEL_KEY),
            assertion_verifier=AppAssertionVerifier(
                parse_trusted_apps(
                    audience=settings.assertion_audience,
                    raw_json=settings.trusted_apps_json,
                )
            ),
            identity_service=self.identities,
        )

    def bootstrap_providers(self) -> None:
        bootstrap_providers()
        self.agent_runs.register_local_models()

    async def bootstrap(self) -> dict[str, Any]:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("select 1"))
        principal = await self.sessions.ensure_principal()
        return {
            "database": {"ok": True, "url": self.settings.database_url},
            "principal": principal_payload(principal),
            "sessions": await self.sessions.list_sessions(),
            "resources": (await self.resources.list_resources())["resources"],
            "defaults": {
                "agentName": self.resources.default_agent_name,
                "modelKey": self.agent_runs.default_model_selection(),
            },
            "models": self.agent_runs.model_choices(),
        }


def load_dotenv(path: Path) -> None:
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
