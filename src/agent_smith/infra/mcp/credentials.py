"""Credential abstractions for MCP servers.

Credentials are intentionally separate from ResourceStore records. Resources
describe configured servers; credential stores provide runtime secret overlays.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.infra.config import get_settings
from agent_smith.infra.db.models.mcp import McpCredentialRecord
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


class PostgresMcpCredentialStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        codec: McpCredentialCodec | None = None,
        fernet_key: str | bytes | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._codec = codec or FernetMcpCredentialCodec(
            fernet_key or _required_settings_key()
        )

    async def set_credential(
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
        principal_key = _key_value(principal_id)
        auth_ref_key = _key_value(auth_ref)
        encrypted_payload = self._codec.encrypt(resolved)

        async with self._session_factory() as db, db.begin():
            row = await self._get_row_for_update(
                db,
                principal_key=principal_key,
                server_name=server_name,
                auth_ref_key=auth_ref_key,
                include_deleted=True,
            )
            if row is None:
                db.add(
                    McpCredentialRecord(
                        principal_key=principal_key,
                        server_name=server_name,
                        auth_ref_key=auth_ref_key,
                        encrypted_payload=encrypted_payload,
                        encryption_scheme=MCP_CREDENTIAL_ENCRYPTION_SCHEME,
                        disabled=disabled,
                        expires_at=expires_at,
                    )
                )
            else:
                row.encrypted_payload = encrypted_payload
                row.encryption_scheme = MCP_CREDENTIAL_ENCRYPTION_SCHEME
                row.disabled = disabled
                row.expires_at = expires_at
                row.deleted_at = None

    async def delete_credential(
        self,
        *,
        principal_id: str | None,
        server_name: str,
        auth_ref: str | None = None,
    ) -> None:
        async with self._session_factory() as db, db.begin():
            row = await self._get_row_for_update(
                db,
                principal_key=_key_value(principal_id),
                server_name=server_name,
                auth_ref_key=_key_value(auth_ref),
            )
            if row is not None:
                row.deleted_at = datetime.now(UTC)

    async def get_credential(
        self,
        *,
        principal_id: str | None,
        server_name: str,
        auth_ref: str | None,
    ) -> McpCredential | None:
        lookup_keys = [
            (_key_value(candidate_principal), candidate_server, _key_value(candidate_auth_ref))
            for candidate_principal, candidate_server, candidate_auth_ref in _lookup_keys(
                principal_id,
                server_name,
                auth_ref,
            )
        ]
        principal_keys = {principal_key for principal_key, _, _ in lookup_keys}
        auth_ref_keys = {auth_ref_key for _, _, auth_ref_key in lookup_keys}
        now = datetime.now(UTC)

        async with self._session_factory() as db:
            rows = list(
                await db.scalars(
                    select(McpCredentialRecord).where(
                        McpCredentialRecord.server_name == server_name,
                        McpCredentialRecord.principal_key.in_(principal_keys),
                        McpCredentialRecord.auth_ref_key.in_(auth_ref_keys),
                        McpCredentialRecord.deleted_at.is_(None),
                        McpCredentialRecord.disabled.is_(False),
                    )
                )
            )

        by_key = {
            (row.principal_key, row.server_name, row.auth_ref_key): row
            for row in rows
            if row.expires_at is None or _as_aware(row.expires_at) > now
        }
        for key in lookup_keys:
            row = by_key.get(key)
            if row is not None:
                if row.encryption_scheme != MCP_CREDENTIAL_ENCRYPTION_SCHEME:
                    raise McpCredentialError(
                        f"Unsupported MCP credential encryption scheme: {row.encryption_scheme}"
                    )
                return self._codec.decrypt(row.encrypted_payload)
        return None

    async def _get_row_for_update(
        self,
        db: AsyncSession,
        *,
        principal_key: str,
        server_name: str,
        auth_ref_key: str,
        include_deleted: bool = False,
    ) -> McpCredentialRecord | None:
        statement = (
            select(McpCredentialRecord)
            .where(
                McpCredentialRecord.principal_key == principal_key,
                McpCredentialRecord.server_name == server_name,
                McpCredentialRecord.auth_ref_key == auth_ref_key,
            )
            .with_for_update()
        )
        if not include_deleted:
            statement = statement.where(McpCredentialRecord.deleted_at.is_(None))
        return (await db.scalars(statement)).one_or_none()


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


def _key_value(value: str | None) -> str:
    return value or ""


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _required_settings_key() -> str:
    key = get_settings().mcp_credentials_key
    if not key:
        raise McpCredentialError(
            "mcp_credentials_key is required for PostgresMcpCredentialStore"
        )
    return key
