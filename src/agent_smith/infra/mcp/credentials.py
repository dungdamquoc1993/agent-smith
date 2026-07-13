"""Credential abstractions for MCP servers.

Credentials are intentionally separate from ResourceStore records. Resources
describe configured servers; credential stores provide runtime secret overlays.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from agent_smith.infra.mcp.errors import McpCredentialError
from agent_smith.infra.mcp.types import McpCredential

MCP_CREDENTIAL_ENCRYPTION_SCHEME = "fernet:v1"


class McpCredentialStore(Protocol):
    async def get_credential(
        self,
        *,
        principal_id: str | None,
        server_name: str,
        auth_ref: str | None,
    ) -> McpCredential | None: ...


class MemoryMcpCredentialStore:
    def __init__(self) -> None:
        self._credentials: dict[tuple[str | None, str, str | None], _MemoryCredentialRecord] = {}

    def set_credential(
        self,
        *,
        principal_id: str | None,
        server_name: str,
        auth_ref: str | None = None,
        credential: McpCredential | dict,
        disabled: bool = False,
        expires_at: datetime | None = None,
    ) -> None:
        resolved = (
            credential
            if isinstance(credential, McpCredential)
            else McpCredential.model_validate(credential)
        )
        self._credentials[(principal_id, server_name, auth_ref)] = _MemoryCredentialRecord(
            credential=resolved,
            disabled=disabled,
            expires_at=expires_at,
        )

    def delete_credential(
        self,
        *,
        principal_id: str | None,
        server_name: str,
        auth_ref: str | None = None,
    ) -> None:
        self._credentials.pop((principal_id, server_name, auth_ref), None)

    async def get_credential(
        self,
        *,
        principal_id: str | None,
        server_name: str,
        auth_ref: str | None,
    ) -> McpCredential | None:
        for key in _lookup_keys(principal_id, server_name, auth_ref):
            record = self._credentials.get(key)
            if record is not None and record.is_active():
                return record.credential
        return None


class McpCredentialCodec(Protocol):
    def encrypt(self, credential: McpCredential) -> str: ...

    def decrypt(self, payload: str) -> McpCredential: ...


class FernetMcpCredentialCodec:
    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except Exception as exc:
            raise McpCredentialError("Invalid MCP credential encryption key") from exc

    def encrypt(self, credential: McpCredential) -> str:
        payload = json.dumps(
            credential.model_dump(mode="json", by_alias=True),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("utf-8")

    def decrypt(self, payload: str) -> McpCredential:
        try:
            decrypted = self._fernet.decrypt(payload.encode("utf-8"))
            return McpCredential.model_validate(json.loads(decrypted.decode("utf-8")))
        except (InvalidToken, json.JSONDecodeError, ValueError) as exc:
            raise McpCredentialError("Unable to decrypt MCP credential") from exc


class _MemoryCredentialRecord:
    def __init__(
        self,
        *,
        credential: McpCredential,
        disabled: bool,
        expires_at: datetime | None,
    ) -> None:
        self.credential = credential
        self.disabled = disabled
        self.expires_at = expires_at

    def is_active(self) -> bool:
        return not self.disabled and (
            self.expires_at is None or _as_aware(self.expires_at) > datetime.now(UTC)
        )


def generate_mcp_credentials_key() -> str:
    return Fernet.generate_key().decode("utf-8")


def _lookup_keys(
    principal_id: str | None,
    server_name: str,
    auth_ref: str | None,
) -> tuple[tuple[str | None, str, str | None], ...]:
    return (
        (principal_id, server_name, auth_ref),
        (None, server_name, auth_ref),
        (principal_id, server_name, None),
        (None, server_name, None),
    )


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
