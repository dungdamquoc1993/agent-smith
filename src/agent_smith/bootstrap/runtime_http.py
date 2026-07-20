"""Runtime HTTP process composition root."""

from __future__ import annotations

import os

from agent_smith.app.auth import AppAssertionVerifier, parse_trusted_apps
from agent_smith.app.services.agent_runs import AgentRunService
from agent_smith.app.services.attachments import AttachmentService
from agent_smith.app.services.authentication import PrincipalAuthenticationService
from agent_smith.app.services.files import FileService
from agent_smith.app.services.identity import PrincipalIdentityService
from agent_smith.app.services.provider_auth import (
    IdentityProviderAuthService,
    IdentityProviderSecretCodec,
)
from agent_smith.app.services.resources import ResourceService
from agent_smith.app.services.runtime import RuntimeService
from agent_smith.app.services.sessions import SessionService
from agent_smith.bootstrap.common import create_blob_store, create_postgres_runtime
from agent_smith.core.llm import bootstrap_providers
from agent_smith.infra.config import RuntimeSettings
from agent_smith.infra.document_processing import inspect_image
from agent_smith.infra.storage.postgres import PostgresRuntime
from agent_smith.infra.storage.postgres.adapters import (
    PostgresFileAuditStore,
    PostgresAgentRunStore,
    PostgresFileCatalog,
    PostgresFileDerivativeReader,
    PostgresFileProcessingRepository,
    PostgresIdentityProviderAuthStore,
    PostgresPrincipalIdentityStore,
    PostgresPrincipalSessionDirectory,
    PostgresRecentConversationProvider,
    PostgresResourceStore,
    PostgresSessionCatalog,
)

DEFAULT_PRINCIPAL_DISPLAY_NAME = "Test Principal"
DEFAULT_AGENT_NAME = "test_assistant"


class RuntimeHttpContainer:
    def __init__(
        self,
        *,
        settings: RuntimeSettings,
        runtime: RuntimeService,
        sessions: SessionService,
        authentication: PrincipalAuthenticationService,
        resources: ResourceService,
        files: FileService,
        agent_runs: AgentRunService,
        postgres_runtime: PostgresRuntime,
    ) -> None:
        self.settings = settings
        self.runtime = runtime
        self.sessions = sessions
        self.authentication = authentication
        self.resources = resources
        self.files = files
        self.agent_runs = agent_runs
        self._postgres_runtime = postgres_runtime
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._postgres_runtime.close()


def build_runtime_http_container(settings: RuntimeSettings) -> RuntimeHttpContainer:
    bootstrap_providers()
    postgres = create_postgres_runtime(settings)
    session_factory = postgres.session_factory

    sessions = SessionService(
        PostgresPrincipalSessionDirectory(session_factory),
        PostgresSessionCatalog(session_factory),
        principal_display_name=os.environ.get(
            "AGENT_SMITH_TEST_PRINCIPAL_NAME", DEFAULT_PRINCIPAL_DISPLAY_NAME
        ),
    )
    resources = ResourceService(
        PostgresResourceStore(session_factory),
        default_agent_name=os.environ.get("AGENT_SMITH_TEST_AGENT_NAME", DEFAULT_AGENT_NAME),
    )

    principal_identities = PrincipalIdentityService(
        PostgresPrincipalIdentityStore(session_factory)
    )
    assertion_verifier = AppAssertionVerifier(
        parse_trusted_apps(
            audience=settings.assertion_audience,
            raw_json=settings.trusted_apps_json,
        )
    )
    secret_codec = (
        IdentityProviderSecretCodec(settings.identity_secrets_key)
        if settings.identity_secrets_key
        else None
    )
    provider_auth = IdentityProviderAuthService(
        PostgresIdentityProviderAuthStore(session_factory),
        assertion_verifier=assertion_verifier,
        secret_codec=secret_codec,
    )
    authentication = PrincipalAuthenticationService(provider_auth, principal_identities)
    catalog = PostgresFileCatalog(session_factory)
    audit_store = PostgresFileAuditStore(session_factory)
    processing_repository = PostgresFileProcessingRepository(session_factory)
    derivative_reader = PostgresFileDerivativeReader(session_factory)
    blobs = create_blob_store(settings)
    files = FileService(
        catalog,
        blobs,
        max_bytes=settings.file_max_bytes,
        presign_ttl_seconds=settings.s3_presign_ttl_seconds,
        processing_repository=processing_repository,
        image_inspector=inspect_image,
        processing_pipeline_version=settings.file_processing_pipeline_version,
        processing_max_attempts=settings.file_processing_max_attempts,
        audit_store=audit_store,
        principal_quota_bytes=settings.file_principal_quota_bytes,
        max_pending_uploads=settings.file_max_pending_uploads,
        init_rate_per_minute=settings.file_init_rate_per_minute,
        complete_rate_per_minute=settings.file_complete_rate_per_minute,
    )
    attachments = AttachmentService(
        catalog,
        blobs,
        max_attachments=settings.attachment_max_count,
        max_materialized_bytes=settings.attachment_max_materialized_bytes,
        read_concurrency=settings.attachment_read_concurrency,
        derivative_reader=derivative_reader,
        max_document_context_tokens=settings.attachment_document_context_max_tokens,
    )
    agent_runs = AgentRunService(
        session_service=sessions,
        resource_service=resources,
        default_permission_mode=settings.default_permission_mode,
        default_model_key=settings.default_model,
        authentication_service=authentication,
        recent_conversation_provider=PostgresRecentConversationProvider(session_factory),
        attachment_service=attachments,
        file_audit_store=audit_store,
        run_store=PostgresAgentRunStore(session_factory),
    )
    runtime = RuntimeService(
        postgres,
        sessions,
        resources,
        agent_runs,
        postgres_url=settings.postgres_url,
    )
    return RuntimeHttpContainer(
        settings=settings,
        runtime=runtime,
        sessions=sessions,
        authentication=authentication,
        resources=resources,
        files=files,
        agent_runs=agent_runs,
        postgres_runtime=postgres,
    )
