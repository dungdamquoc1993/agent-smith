from __future__ import annotations

import uuid
from os import getenv

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.core.agent.persistence import FileReferenceContent, PersistedUserMessage
from agent_smith.core.llm.types import TextContent
from agent_smith.infra.storage.postgres.adapters.sessions import PostgresSessionCatalog
from agent_smith.infra.storage.postgres.database import Base
from agent_smith.infra.storage.postgres.models.file import File, FileStatus
from agent_smith.infra.storage.postgres.models.principal import Principal
from agent_smith.infra.storage.postgres.models.session import (
    Session as DbSession,
    SessionEntry as DbSessionEntry,
    SessionEntryFile,
)


@pytest.mark.asyncio
async def test_session_file_bindings_are_atomic_restricted_and_cloned_on_fork() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")

    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = PostgresSessionCatalog(factory)
    principal_id = uuid.uuid4()
    file_id = uuid.uuid4()
    source_id: uuid.UUID | None = None
    fork_id: uuid.UUID | None = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add(Principal(id=principal_id, display_name=f"Attachment {principal_id}"))
            db.add(
                File(
                    id=file_id,
                    principal_id=principal_id,
                    original_name="photo.png",
                    mime_type="image/png",
                    size_bytes=8,
                    object_key=f"objects/{file_id}",
                    status=FileStatus.ready,
                )
            )

        source = await catalog.create(principal_id=str(principal_id), title="source")
        source_id = uuid.UUID((await source.get_metadata()).id)
        reference = FileReferenceContent(
            fileId=str(file_id), mimeType="image/png", displayName="photo.png"
        )
        entry_id = await source.append_message(
            PersistedUserMessage(
                content=[TextContent(text="inspect"), reference],
                timestamp=1,
            )
        )
        async with factory() as db:
            binding = await db.scalar(
                select(SessionEntryFile).where(
                    SessionEntryFile.session_entry_id == uuid.UUID(entry_id)
                )
            )
            assert binding is not None
            assert binding.file_id == file_id
            assert binding.position == 1
            assert binding.purpose == "input"

        with pytest.raises(ValueError, match="Attachment"):
            await source.append_message(
                PersistedUserMessage(
                    content=[
                        reference,
                        FileReferenceContent(
                            fileId=str(uuid.uuid4()),
                            mimeType="image/png",
                            displayName="missing.png",
                        ),
                    ],
                    timestamp=2,
                )
            )
        async with factory() as db:
            assert await db.scalar(
                select(func.count()).select_from(DbSessionEntry).where(
                    DbSessionEntry.session_id == source_id
                )
            ) == 1
            assert await db.scalar(
                select(func.count())
                .select_from(SessionEntryFile)
                .join(DbSessionEntry, DbSessionEntry.id == SessionEntryFile.session_entry_id)
                .where(DbSessionEntry.session_id == source_id)
            ) == 1

        fork = await catalog.fork(
            await source.get_metadata(),
            principal_id=str(principal_id),
            title="fork",
        )
        fork_id = uuid.UUID((await fork.get_metadata()).id)
        async with factory() as db:
            fork_binding = await db.scalar(
                select(SessionEntryFile)
                .join(DbSessionEntry, DbSessionEntry.id == SessionEntryFile.session_entry_id)
                .where(DbSessionEntry.session_id == fork_id)
            )
            assert fork_binding is not None and fork_binding.file_id == file_id

        with pytest.raises(IntegrityError):
            async with factory() as db, db.begin():
                await db.execute(delete(File).where(File.id == file_id))
    finally:
        async with factory() as db, db.begin():
            ids = [value for value in (source_id, fork_id) if value is not None]
            if ids:
                await db.execute(delete(DbSession).where(DbSession.id.in_(ids)))
            await db.execute(delete(File).where(File.id == file_id))
            await db.execute(delete(Principal).where(Principal.id == principal_id))
        await engine.dispose()
