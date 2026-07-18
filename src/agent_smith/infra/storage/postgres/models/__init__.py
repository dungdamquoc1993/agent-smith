"""SQLAlchemy models owned by the Postgres backend."""

from agent_smith.infra.storage.postgres.models.app_assertions import AppAssertionNonce
from agent_smith.infra.storage.postgres.models.file_audit import FileAuditEvent
from agent_smith.infra.storage.postgres.models.file_processing import (
    FileDerivative,
    FileProcessingJob,
    ProcessingJobStatus,
)
from agent_smith.infra.storage.postgres.models.files import File, FileStatus
from agent_smith.infra.storage.postgres.models.identity_providers import (
    ExternalIdentity,
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
    IdentityProviderKeyStatus,
    IdentityProviderStatus,
)
from agent_smith.infra.storage.postgres.models.mcp_credentials import McpCredentialRecord
from agent_smith.infra.storage.postgres.models.principals import Principal, PrincipalStatus
from agent_smith.infra.storage.postgres.models.resources import Resource, ResourceVersion
from agent_smith.infra.storage.postgres.models.sessions import (
    Session,
    SessionEntry,
    SessionEntryFile,
    SessionEntryType,
    SessionKind,
)

__all__ = [
    "File",
    "FileStatus",
    "FileAuditEvent",
    "FileDerivative",
    "FileProcessingJob",
    "ProcessingJobStatus",
    "McpCredentialRecord",
    "Principal",
    "PrincipalStatus",
    "IdentityProvider",
    "IdentityProviderApiKey",
    "IdentityProviderAssertionKey",
    "IdentityProviderKeyStatus",
    "IdentityProviderStatus",
    "ExternalIdentity",
    "AppAssertionNonce",
    "Resource",
    "ResourceVersion",
    "Session",
    "SessionEntry",
    "SessionEntryFile",
    "SessionEntryType",
    "SessionKind",
]
