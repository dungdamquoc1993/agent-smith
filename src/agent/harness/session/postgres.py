"""Postgres-backed harness session storage."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.session import (
    Session as DbSession,
    SessionEntry as DbSessionEntry,
    SessionEntryType as DbSessionEntryType,
)
from agent.harness.session.session import Session
from agent.harness.session.types import (
    SessionEntryType,
    SessionMetadata,
    SessionTreeEntry,
)


def _uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _metadata_from_row(row: DbSession) -> SessionMetadata:
    return SessionMetadata(
        id=str(row.id),
        principal_id=str(row.principal_id),
        title=row.title,
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
            row = await db.get(DbSession, _uuid(self._metadata.id))
            if row is None:
                raise ValueError(f"Session {self._metadata.id} not found")
            return _metadata_from_row(row)

    async def create_entry_id(self) -> str:
        return str(uuid.uuid4())

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        async with self._session_factory() as db, db.begin():
            session_row = await db.get(DbSession, _uuid(self._metadata.id))
            if session_row is None:
                raise ValueError(f"Session {self._metadata.id} not found")
            db.add(
                DbSessionEntry(
                    id=_uuid(entry.id),
                    session_id=session_row.id,
                    parent_id=_uuid(entry.parent_id),
                    type=DbSessionEntryType(entry.type),
                    payload=_entry_payload(entry),
                    principal_id=session_row.principal_id,
                )
            )
            session_row.current_leaf_id = _uuid(entry.id)

    async def get_entry(self, entry_id: str) -> SessionTreeEntry | None:
        async with self._session_factory() as db:
            row = await db.get(DbSessionEntry, _uuid(entry_id))
            if row is None or str(row.session_id) != self._metadata.id:
                return None
            return _row_to_entry(row)

    async def find_entries(self, entry_type: SessionEntryType) -> list[SessionTreeEntry]:
        async with self._session_factory() as db:
            result = await db.scalars(
                select(DbSessionEntry)
                .where(
                    DbSessionEntry.session_id == _uuid(self._metadata.id),
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
                .where(DbSessionEntry.session_id == _uuid(self._metadata.id))
                .order_by(DbSessionEntry.created_at, DbSessionEntry.id)
            )
            return [_row_to_entry(row) for row in result]

    async def get_leaf_id(self) -> str | None:
        async with self._session_factory() as db:
            row = await db.get(DbSession, _uuid(self._metadata.id))
            if row is None:
                raise ValueError(f"Session {self._metadata.id} not found")
            return str(row.current_leaf_id) if row.current_leaf_id else None

    async def set_leaf_id(self, entry_id: str | None) -> None:
        async with self._session_factory() as db, db.begin():
            session_row = await db.get(DbSession, _uuid(self._metadata.id))
            if session_row is None:
                raise ValueError(f"Session {self._metadata.id} not found")
            if entry_id is not None:
                entry = await db.get(DbSessionEntry, _uuid(entry_id))
                if entry is None or entry.session_id != session_row.id:
                    raise ValueError(f"Entry {entry_id} not found")
            session_row.current_leaf_id = _uuid(entry_id)


class PostgresSessionRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, **options: Any) -> Session:
        principal_id = options.get("principal_id")
        if principal_id is None:
            raise ValueError("PostgresSessionRepo.create() requires principal_id")
        session_id = _uuid(options.get("id")) or uuid.uuid4()
        async with self._session_factory() as db, db.begin():
            row = DbSession(
                id=session_id,
                principal_id=_uuid(principal_id),
                title=options.get("title"),
            )
            db.add(row)
        metadata = SessionMetadata(
            id=str(session_id),
            principal_id=str(principal_id),
            title=options.get("title"),
        )
        return Session(PostgresSessionStorage(self._session_factory, metadata))

    async def open(self, metadata: SessionMetadata | dict[str, Any]) -> Session:
        resolved = (
            metadata if isinstance(metadata, SessionMetadata) else SessionMetadata.model_validate(metadata)
        )
        async with self._session_factory() as db:
            row = await db.get(DbSession, _uuid(resolved.id))
            if row is None:
                raise ValueError(f"Session {resolved.id} not found")
            return Session(PostgresSessionStorage(self._session_factory, _metadata_from_row(row)))

    async def fork(
        self,
        source: SessionMetadata | dict[str, Any],
        **options: Any,
    ) -> Session:
        source_session = await self.open(source)
        source_metadata = await source_session.get_metadata()
        principal_id = options.get("principal_id") or source_metadata.principal_id
        target_session = await self.create(
            principal_id=principal_id,
            title=options.get("title"),
            id=options.get("id"),
        )
        target_storage = target_session.get_storage()
        source_leaf_id = options.get("entry_id") or await source_session.get_leaf_id()
        id_map: dict[str, str] = {}
        for entry in await source_session.get_branch(source_leaf_id):
            new_id = await target_storage.create_entry_id()
            id_map[entry.id] = new_id
            cloned = entry.model_copy(
                update={
                    "id": new_id,
                    "parent_id": id_map.get(entry.parent_id) if entry.parent_id else None,
                }
            )
            await target_storage.append_entry(cloned)
        return target_session
