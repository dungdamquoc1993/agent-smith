"""Postgres session-catalog capability."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.core.agent.harness.session.session import Session
from agent_smith.core.agent.harness.session.types import SessionMetadata
from agent_smith.infra.storage.postgres.adapters.sessions.records import (
    enum_value,
    metadata_from_row,
    rows_to_branch,
    uuid_value,
)
from agent_smith.infra.storage.postgres.adapters.sessions.storage import (
    PostgresSessionStorage,
)
from agent_smith.infra.storage.postgres.models.sessions import (
    Session as DbSession,
    SessionEntry as DbSessionEntry,
    SessionEntryFile,
    SessionKind as DbSessionKind,
)


class PostgresSessionCatalog:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, **options: Any) -> Session:
        principal_id = options.get("principal_id")
        if principal_id is None:
            raise ValueError("PostgresSessionCatalog.create() requires principal_id")
        session_id = uuid_value(options.get("id")) or uuid.uuid4()
        async with self._session_factory() as db, db.begin():
            row = DbSession(
                id=session_id,
                principal_id=uuid_value(principal_id),
                title=options.get("title"),
                kind=DbSessionKind(options.get("kind", "chat")),
                parent_session_id=uuid_value(options.get("parent_session_id")),
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
            row = await db.get(DbSession, uuid_value(resolved.id))
            if row is None:
                raise ValueError(f"Session {resolved.id} not found")
            return Session(
                PostgresSessionStorage(self._session_factory, metadata_from_row(row))
            )

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
        source_id = uuid_value(resolved.id)
        target_id = uuid_value(options.get("id")) or uuid.uuid4()

        async with self._session_factory() as db, db.begin():
            source_row = await db.get(DbSession, source_id)
            if source_row is None:
                raise ValueError(f"Session {resolved.id} not found")

            target_row = DbSession(
                id=target_id,
                principal_id=uuid_value(options.get("principal_id")) or source_row.principal_id,
                title=options.get("title"),
                kind=DbSessionKind(options.get("kind", enum_value(source_row.kind))),
                parent_session_id=(
                    uuid_value(options["parent_session_id"])
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
            source_leaf_id = uuid_value(options.get("entry_id")) or source_row.current_leaf_id
            branch = rows_to_branch(source_entries, source_leaf_id)
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

            source_bindings = (
                list(
                    await db.scalars(
                        select(SessionEntryFile).where(
                            SessionEntryFile.session_entry_id.in_(
                                [entry.id for entry in branch]
                            )
                        )
                    )
                )
                if branch
                else []
            )
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

            metadata = metadata_from_row(target_row)

        return Session(PostgresSessionStorage(self._session_factory, metadata))


__all__ = ["PostgresSessionCatalog"]
