"""SQLAlchemy models owned by the Postgres backend."""

from agent_smith.infra.storage.postgres.models.file import (
    File,
    FileAuditEvent,
    FileDerivative,
    FileProcessingJob,
)
from agent_smith.infra.storage.postgres.models.mcp import McpCredentialRecord
from agent_smith.infra.storage.postgres.models.principal import (
    AppAssertionNonce,
    ExternalIdentity,
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
    Principal,
)
from agent_smith.infra.storage.postgres.models.resource import Resource, ResourceVersion
from agent_smith.infra.storage.postgres.models.session import Session, SessionEntry, SessionEntryFile

__all__ = [
    "File",
    "FileAuditEvent",
    "FileDerivative",
    "FileProcessingJob",
    "McpCredentialRecord",
    "Principal",
    "IdentityProvider",
    "IdentityProviderApiKey",
    "IdentityProviderAssertionKey",
    "ExternalIdentity",
    "AppAssertionNonce",
    "Resource",
    "ResourceVersion",
    "Session",
    "SessionEntry",
    "SessionEntryFile",
]
