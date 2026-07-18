"""Postgres-backed session storage and capability implementations."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.infra.storage.postgres.adapters.sessions.records import (
    metadata_from_row,
    uuid_value,
)
from agent_smith.infra.storage.postgres.models.sessions import (
    Session as DbSession,
    SessionEntry as DbSessionEntry,
    SessionEntryType as DbSessionEntryType,
    SessionEntryFile,
)
from agent_smith.infra.storage.postgres.models.files import File, FileStatus
from agent_smith.core.agent.persistence import FileReferenceContent
from agent_smith.core.agent.harness.session.types import (
    SessionEntryType,
    SessionMetadata,
    SessionTreeEntry,
)


def _entry_payload(entry: SessionTreeEntry) -> dict[str, Any]:
    return entry.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
        exclude={"id", "type", "parent_id", "timestamp"},
    )


def _row_to_entry(row: DbSessionEntry) -> SessionTreeEntry:
    payload = dict(row.payload or {})
    entry_type = row.type.value if hasattr(row.type, "value") else str(row.type)
    timestamp = row.created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return SessionTreeEntry(
        **payload,
        id=str(row.id),
        type=entry_type,
        parentId=str(row.parent_id) if row.parent_id else None,
        timestamp=timestamp.isoformat(),
    )


def _entry_file_references(entry: SessionTreeEntry) -> list[tuple[int, FileReferenceContent]]:
    if entry.type != "message" or entry.message is None or entry.message.role == "assistant":
        return []
    content = entry.message.content
    if isinstance(content, str):
        return []
    return [
        (position, block)
        for position, block in enumerate(content)
        if isinstance(block, FileReferenceContent)
    ]


class PostgresSessionStorage:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        metadata: SessionMetadata,
    ) -> None:
        self._session_factory = session_factory
        self._metadata = metadata

    async def get_metadata(self) -> SessionMetadata:
        async with self._session_factory() as db:
            row = await db.get(DbSession, uuid_value(self._metadata.id))
            if row is None:
                raise ValueError(f"Session {self._metadata.id} not found")
            return metadata_from_row(row)

    async def create_entry_id(self) -> str:
        return str(uuid.uuid4())

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        async with self._session_factory() as db, db.begin():
            session_row = await db.get(DbSession, uuid_value(self._metadata.id))
            if session_row is None:
                raise ValueError(f"Session {self._metadata.id} not found")
            entry_row = DbSessionEntry(
                id=uuid_value(entry.id),
                session_id=session_row.id,
                parent_id=uuid_value(entry.parent_id),
                type=DbSessionEntryType(entry.type),
                payload=_entry_payload(entry),
                principal_id=session_row.principal_id,
            )
            db.add(entry_row)
            references = _entry_file_references(entry)
            if references:
                ids = [uuid_value(reference.file_id) for _, reference in references]
                rows = list(
                    await db.scalars(
                        select(File).where(File.id.in_(ids)).with_for_update()
                    )
                )
                by_id = {row.id: row for row in rows}
                for position, reference in references:
                    file_id = uuid_value(reference.file_id)
                    file_row = by_id.get(file_id)
                    if (
                        file_row is None
                        or file_row.principal_id != session_row.principal_id
                        or file_row.status != FileStatus.ready
                        or file_row.mime_type != reference.mime_type
                        or file_row.original_name != reference.display_name
                    ):
                        raise ValueError("Attachment is no longer available")
                    db.add(
                        SessionEntryFile(
                            session_entry_id=entry_row.id,
                            file_id=file_id,
                            position=position,
                            purpose="input",
                        )
                    )
            session_row.current_leaf_id = uuid_value(entry.id)

    async def get_entry(self, entry_id: str) -> SessionTreeEntry | None:
        async with self._session_factory() as db:
            row = await db.get(DbSessionEntry, uuid_value(entry_id))
            if row is None or str(row.session_id) != self._metadata.id:
                return None
            return _row_to_entry(row)

    async def find_entries(self, entry_type: SessionEntryType) -> list[SessionTreeEntry]:
        async with self._session_factory() as db:
            result = await db.scalars(
                select(DbSessionEntry)
                .where(
                    DbSessionEntry.session_id == uuid_value(self._metadata.id),
                    DbSessionEntry.type == DbSessionEntryType(entry_type),
                )
                .order_by(DbSessionEntry.created_at, DbSessionEntry.id)
            )
            return [_row_to_entry(row) for row in result]

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]:
        if leaf_id is None:
            return []
        entries = {entry.id: entry for entry in await self.get_entries()}
        path: list[SessionTreeEntry] = []
        current_id: str | None = leaf_id
        while current_id is not None:
            entry = entries.get(current_id)
            if entry is None:
                break
            path.append(entry)
            current_id = entry.parent_id
        return list(reversed(path))

    async def get_entries(self) -> list[SessionTreeEntry]:
        async with self._session_factory() as db:
            result = await db.scalars(
                select(DbSessionEntry)
                .where(DbSessionEntry.session_id == uuid_value(self._metadata.id))
                .order_by(DbSessionEntry.created_at, DbSessionEntry.id)
            )
            return [_row_to_entry(row) for row in result]

    async def get_leaf_id(self) -> str | None:
        async with self._session_factory() as db:
            row = await db.get(DbSession, uuid_value(self._metadata.id))
            if row is None:
                raise ValueError(f"Session {self._metadata.id} not found")
            return str(row.current_leaf_id) if row.current_leaf_id else None

    async def set_leaf_id(self, entry_id: str | None) -> None:
        async with self._session_factory() as db, db.begin():
            session_row = await db.get(DbSession, uuid_value(self._metadata.id))
            if session_row is None:
                raise ValueError(f"Session {self._metadata.id} not found")
            if entry_id is not None:
                entry = await db.get(DbSessionEntry, uuid_value(entry_id))
                if entry is None or entry.session_id != session_row.id:
                    raise ValueError(f"Entry {entry_id} not found")
            session_row.current_leaf_id = uuid_value(entry_id)
