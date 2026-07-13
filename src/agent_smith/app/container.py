"""Application composition root."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_smith.app.auth import AppAssertionVerifier, parse_trusted_apps
from agent_smith.app.services.agent_runs import AgentRunService
from agent_smith.app.services.authentication import PrincipalAuthenticationService
from agent_smith.app.services.files import FileService
from agent_smith.app.services.identity import PrincipalIdentityService
from agent_smith.app.services.identity_providers import IdentityProviderManagementService
from agent_smith.app.services.provider_auth import (
    IdentityProviderAuthService,
    IdentityProviderSecretCodec,
)
from agent_smith.app.services.resources import ResourceService
from agent_smith.app.services.sessions import SessionService, principal_payload
from agent_smith.app.services.tasks import TaskService
from agent_smith.core.llm import bootstrap_providers
from agent_smith.core.tasks import MemoryTaskRuntime
from agent_smith.infra.config import get_settings
from agent_smith.infra.storage.postgres.adapters import (
    PostgresPrincipalSessionDirectory,
    PostgresRecentConversationProvider,
    PostgresResourceStore,
    PostgresIdentityStore,
    PostgresFileCatalog,
    PostgresSessionCatalog,
)
from agent_smith.infra.storage.postgres.database import get_engine, get_session_factory
from agent_smith.infra.storage.s3 import S3BlobStore, create_s3_client


DEFAULT_PRINCIPAL_DISPLAY_NAME = "Test Principal"
DEFAULT_AGENT_NAME = "test_assistant"


class AppContainer:
    def __init__(self) -> None:
        settings = get_settings()
        session_factory = get_session_factory()
        session_catalog = PostgresSessionCatalog(session_factory)
        session_directory = PostgresPrincipalSessionDirectory(session_factory)
        resource_store = PostgresResourceStore(session_factory)
        identity_store = PostgresIdentityStore(session_factory)
        self.settings = settings
        self.sessions = SessionService(
            session_directory,
            session_catalog,
            principal_display_name=os.environ.get(
                "AGENT_SMITH_TEST_PRINCIPAL_NAME",
                DEFAULT_PRINCIPAL_DISPLAY_NAME,
            ),
        )
        self.identities = PrincipalIdentityService(identity_store)
        assertion_verifier = AppAssertionVerifier(
            parse_trusted_apps(
                audience=settings.assertion_audience,
                raw_json=settings.trusted_apps_json,
            )
        )
        identity_secret_codec = (
            IdentityProviderSecretCodec(settings.identity_secrets_key)
            if settings.identity_secrets_key
            else None
        )
        self.provider_auth = IdentityProviderAuthService(
            identity_store,
            assertion_verifier=assertion_verifier,
            secret_codec=identity_secret_codec,
        )
        self.authentication = PrincipalAuthenticationService(
            self.provider_auth,
            self.identities,
        )
        self.identity_providers = IdentityProviderManagementService(
            identity_store,
            secret_codec=identity_secret_codec,
        )
        self.resources = ResourceService(
            resource_store,
            default_agent_name=os.environ.get("AGENT_SMITH_TEST_AGENT_NAME", DEFAULT_AGENT_NAME),
        )
        self.tasks = TaskService(MemoryTaskRuntime())
        self.files = FileService(
            PostgresFileCatalog(session_factory),
            S3BlobStore(
                create_s3_client(
                    endpoint_url=settings.s3_endpoint_url,
                    region=settings.s3_region,
                    access_key_id=settings.s3_access_key_id,
                    secret_access_key=settings.s3_secret_access_key,
                    path_style=settings.s3_path_style,
                ),
                bucket=settings.s3_bucket,
            ),
            max_bytes=settings.file_max_bytes,
            presign_ttl_seconds=settings.s3_presign_ttl_seconds,
            pending_ttl_seconds=settings.file_pending_ttl_seconds,
            deleted_retention_seconds=settings.file_deleted_retention_seconds,
        )
        self.agent_runs = AgentRunService(
            session_service=self.sessions,
            resource_service=self.resources,
            default_permission_mode=settings.default_permission_mode,
            default_model_key=settings.default_model,
            authentication_service=self.authentication,
            recent_conversation_provider=PostgresRecentConversationProvider(session_factory),
        )

    def bootstrap_providers(self) -> None:
        bootstrap_providers()

    async def bootstrap(self) -> dict[str, Any]:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.exec_driver_sql("select 1")
        principal = await self.sessions.ensure_principal()
        return {
            "postgres": {"ok": True, "url": self.settings.postgres_url},
            "principal": principal_payload(principal),
            "sessions": await self.sessions.list_sessions(),
            "resources": (await self.resources.list_resources())["resources"],
            **self.model_catalog(),
        }

    def model_catalog(self) -> dict[str, Any]:
        return {
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
