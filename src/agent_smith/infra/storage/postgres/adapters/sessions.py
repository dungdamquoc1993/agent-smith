"""Postgres-backed harness session storage."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.sessions import PrincipalRecord, SessionRecord
from agent_smith.core.agent.harness.context_types import RecentConversationSnapshot
from agent_smith.infra.storage.postgres.models.principal import Principal
from agent_smith.infra.storage.postgres.models.session import (
    Session as DbSession,
    SessionEntry as DbSessionEntry,
    SessionEntryType as DbSessionEntryType,
    SessionKind as DbSessionKind,
    SessionEntryFile,
)
from agent_smith.infra.storage.postgres.models.file import File, FileStatus
from agent_smith.core.agent.persistence import FileReferenceContent
from agent_smith.core.agent.harness.session.session import Session
from agent_smith.core.agent.harness.session.types import (
    SessionEntryType,
    SessionMetadata,
    SessionTreeEntry,
)


def _uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _metadata_from_row(row: DbSession) -> SessionMetadata:
    kind = row.kind.value if hasattr(row.kind, "value") else str(row.kind)
    return SessionMetadata(
        id=str(row.id),
        principal_id=str(row.principal_id),
        title=row.title,
        kind=kind,
        parent_session_id=str(row.parent_session_id) if row.parent_session_id else None,
        agent_name=row.agent_name,
        origin_task_id=row.origin_task_id,
        provenance=dict(row.provenance or {}),
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
            entry_row = DbSessionEntry(
                id=_uuid(entry.id),
                session_id=session_row.id,
                parent_id=_uuid(entry.parent_id),
                type=DbSessionEntryType(entry.type),
                payload=_entry_payload(entry),
                principal_id=session_row.principal_id,
            )
            db.add(entry_row)
            references = _entry_file_references(entry)
            if references:
                ids = [_uuid(reference.file_id) for _, reference in references]
                rows = list(
                    await db.scalars(
                        select(File).where(File.id.in_(ids)).with_for_update()
                    )
                )
                by_id = {row.id: row for row in rows}
                for position, reference in references:
                    file_id = _uuid(reference.file_id)
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


class PostgresPrincipalSessionDirectory:
    """App-facing principal and session discovery queries."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_principal(self, display_name: str) -> PrincipalRecord:
        async with self._session_factory() as db, db.begin():
            principal = (
                await db.scalars(
                    select(Principal)
                    .where(Principal.display_name == display_name)
                    .order_by(Principal.created_at, Principal.id)
                )
            ).first()
            if principal is None:
                principal = Principal(id=uuid.uuid4(), display_name=display_name)
                db.add(principal)
                await db.flush()
            await db.refresh(principal)
            return _principal_record(principal)

    async def list_sessions(
        self,
        *,
        principal_id: str,
        limit: int = 25,
    ) -> list[SessionRecord]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(DbSession)
                    .where(DbSession.principal_id == _uuid(principal_id))
                    .order_by(DbSession.updated_at.desc(), DbSession.created_at.desc())
                    .limit(limit)
                )
            ).all()
            return [_session_record(row) for row in rows]

    async def session_belongs_to(self, *, session_id: str, principal_id: str) -> bool:
        async with self._session_factory() as db:
            row = await db.get(DbSession, _uuid(session_id))
            return row is not None and row.principal_id == _uuid(principal_id)


class PostgresSessionCatalog:
    """App-facing session lifecycle adapter backed by Postgres.

    Harness storage operations remain short-lived and independently atomic.
    Lifecycle operations that require a wider boundary, such as ``fork``, are
    completed here in one database transaction.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, **options: Any) -> Session:
        principal_id = options.get("principal_id")
        if principal_id is None:
            raise ValueError("PostgresSessionCatalog.create() requires principal_id")
        session_id = _uuid(options.get("id")) or uuid.uuid4()
        async with self._session_factory() as db, db.begin():
            row = DbSession(
                id=session_id,
                principal_id=_uuid(principal_id),
                title=options.get("title"),
                kind=DbSessionKind(options.get("kind", "chat")),
                parent_session_id=_uuid(options.get("parent_session_id")),
                agent_name=options.get("agent_name"),
                origin_task_id=options.get("origin_task_id"),
                provenance=dict(options.get("provenance") or {}),
            )
            db.add(row)
        metadata = SessionMetadata(
            id=str(session_id),
            principal_id=str(principal_id),
            title=options.get("title"),
            kind=options.get("kind", "chat"),
            parent_session_id=options.get("parent_session_id"),
            agent_name=options.get("agent_name"),
            origin_task_id=options.get("origin_task_id"),
            provenance=dict(options.get("provenance") or {}),
        )
        return Session(PostgresSessionStorage(self._session_factory, metadata))

    async def open(self, metadata: SessionMetadata | dict[str, Any]) -> Session:
        resolved = (
            metadata
            if isinstance(metadata, SessionMetadata)
            else SessionMetadata.model_validate(metadata)
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
        resolved = (
            source
            if isinstance(source, SessionMetadata)
            else SessionMetadata.model_validate(source)
        )
        source_id = _uuid(resolved.id)
        target_id = _uuid(options.get("id")) or uuid.uuid4()

        async with self._session_factory() as db, db.begin():
            source_row = await db.get(DbSession, source_id)
            if source_row is None:
                raise ValueError(f"Session {resolved.id} not found")

            target_row = DbSession(
                id=target_id,
                principal_id=_uuid(options.get("principal_id")) or source_row.principal_id,
                title=options.get("title"),
                kind=DbSessionKind(options.get("kind", _enum_value(source_row.kind))),
                parent_session_id=(
                    _uuid(options["parent_session_id"])
                    if "parent_session_id" in options
                    else source_row.parent_session_id
                ),
                agent_name=options.get("agent_name", source_row.agent_name),
                origin_task_id=options.get("origin_task_id", source_row.origin_task_id),
                provenance=dict(options.get("provenance", source_row.provenance) or {}),
            )
            db.add(target_row)
            await db.flush()

            source_entries = list(
                await db.scalars(
                    select(DbSessionEntry)
                    .where(DbSessionEntry.session_id == source_row.id)
                    .order_by(DbSessionEntry.created_at, DbSessionEntry.id)
                )
            )
            source_leaf_id = _uuid(options.get("entry_id")) or source_row.current_leaf_id
            branch = _rows_to_branch(source_entries, source_leaf_id)
            id_map: dict[uuid.UUID, uuid.UUID] = {}
            for entry in branch:
                new_id = uuid.uuid4()
                id_map[entry.id] = new_id
                db.add(
                    DbSessionEntry(
                        id=new_id,
                        session_id=target_row.id,
                        parent_id=id_map.get(entry.parent_id),
                        type=entry.type,
                        payload=dict(entry.payload or {}),
                        principal_id=target_row.principal_id,
                    )
                )
                target_row.current_leaf_id = new_id

            source_bindings = list(
                await db.scalars(
                    select(SessionEntryFile).where(
                        SessionEntryFile.session_entry_id.in_([entry.id for entry in branch])
                    )
                )
            ) if branch else []
            if source_bindings and target_row.principal_id != source_row.principal_id:
                raise ValueError("Cannot fork file-bound session history across principals")
            for binding in source_bindings:
                db.add(
                    SessionEntryFile(
                        session_entry_id=id_map[binding.session_entry_id],
                        file_id=binding.file_id,
                        position=binding.position,
                        purpose=binding.purpose,
                    )
                )

            metadata = _metadata_from_row(target_row)

        return Session(PostgresSessionStorage(self._session_factory, metadata))


class PostgresRecentConversationProvider:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_recent_conversations(
        self,
        *,
        principal_id: str,
        current_session_id: str,
        limit: int = 40,
    ) -> list[RecentConversationSnapshot]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(DbSession)
                    .where(
                        DbSession.principal_id == _uuid(principal_id),
                        DbSession.kind == DbSessionKind.chat,
                        DbSession.id != _uuid(current_session_id),
                    )
                    .order_by(DbSession.updated_at.desc(), DbSession.created_at.desc())
                    .limit(limit)
                )
            ).all()

        catalog = PostgresSessionCatalog(self._session_factory)
        snapshots: list[RecentConversationSnapshot] = []
        for row in rows:
            metadata = _metadata_from_row(row)
            session = await catalog.open(metadata)
            context = await session.build_context()
            snapshots.append(
                RecentConversationSnapshot(
                    id=metadata.id,
                    title=metadata.title,
                    updated_at=row.updated_at.isoformat() if row.updated_at else None,
                    messages=context.messages,
                )
            )
        return snapshots


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _principal_record(row: Principal) -> PrincipalRecord:
    return PrincipalRecord(
        id=str(row.id),
        display_name=row.display_name,
        status=_enum_value(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _session_record(row: DbSession) -> SessionRecord:
    return SessionRecord(
        id=str(row.id),
        principal_id=str(row.principal_id),
        title=row.title,
        kind=_enum_value(row.kind),
        parent_session_id=str(row.parent_session_id) if row.parent_session_id else None,
        agent_name=row.agent_name,
        origin_task_id=row.origin_task_id,
        current_leaf_id=str(row.current_leaf_id) if row.current_leaf_id else None,
        provenance=dict(row.provenance or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _rows_to_branch(
    entries: list[DbSessionEntry],
    leaf_id: uuid.UUID | None,
) -> list[DbSessionEntry]:
    if leaf_id is None:
        return []
    by_id = {entry.id: entry for entry in entries}
    path: list[DbSessionEntry] = []
    current_id: uuid.UUID | None = leaf_id
    while current_id is not None:
        entry = by_id.get(current_id)
        if entry is None:
            raise ValueError(f"Entry {current_id} not found in source session")
        path.append(entry)
        current_id = entry.parent_id
    return list(reversed(path))
