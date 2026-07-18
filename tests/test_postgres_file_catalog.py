from __future__ import annotations

import uuid
from os import getenv

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.app.ports.files import PendingFileRecord
from agent_smith.infra.storage.postgres.adapters import PostgresFileCatalog
from agent_smith.infra.storage.postgres.database import Base
from agent_smith.infra.storage.postgres.models.files import File
from agent_smith.infra.storage.postgres.models.principals import Principal


@pytest.mark.asyncio
async def test_file_catalog_ownership_and_transition_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = PostgresFileCatalog(factory)
    principal_id = uuid.uuid4()
    other_id = uuid.uuid4()
    file_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add_all(
                [
                    Principal(id=principal_id, display_name="File Owner"),
                    Principal(id=other_id, display_name="Other Owner"),
                ]
            )
        record = await catalog.create_pending(
            PendingFileRecord(
                id=str(file_id),
                principal_id=str(principal_id),
                original_name="a.txt",
                mime_type="text/plain",
                size_bytes=1,
                object_key=f"principals/{principal_id}/files/{file_id}/original",
            )
        )
        assert await catalog.get_file(file_id=record.id, principal_id=str(other_id)) is None
        uploaded = await catalog.mark_uploaded(
            file_id=record.id,
            principal_id=str(principal_id),
            mime_type="text/plain",
            etag="etag",
            sha256=None,
        )
        assert uploaded is not None and uploaded.status == "uploaded"
        assert (
            await catalog.mark_uploaded(
                file_id=record.id,
                principal_id=str(principal_id),
                mime_type="text/plain",
                etag="etag",
                sha256=None,
            )
            is None
        )
    finally:
        async with factory() as db, db.begin():
            await db.execute(delete(File).where(File.id == file_id))
            await db.execute(delete(Principal).where(Principal.id.in_([principal_id, other_id])))
        await engine.dispose()
