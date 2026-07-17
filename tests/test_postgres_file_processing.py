from __future__ import annotations

import uuid
from os import getenv

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.app.ports.document_processing import PendingDerivative
from agent_smith.app.ports.files import PendingFileRecord
from agent_smith.infra.storage.postgres.adapters import (
    PostgresFileCatalog,
    PostgresFileProcessingStore,
)
from agent_smith.infra.storage.postgres.database import Base
from agent_smith.infra.storage.postgres.models.file import File
from agent_smith.infra.storage.postgres.models.principal import Principal


@pytest.mark.asyncio
async def test_postgres_processing_queue_claim_and_finalize_when_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = PostgresFileCatalog(factory)
    processing = PostgresFileProcessingStore(factory)
    principal_id = uuid.uuid4()
    file_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add(Principal(id=principal_id, display_name="Processing Owner"))
        await catalog.create_pending(
            PendingFileRecord(
                id=str(file_id),
                principal_id=str(principal_id),
                original_name="a.txt",
                mime_type="text/plain",
                size_bytes=1,
                object_key=f"principals/{principal_id}/files/{file_id}/original",
            )
        )
        queued = await processing.mark_uploaded_and_enqueue(
            file_id=str(file_id),
            principal_id=str(principal_id),
            etag="etag",
            sha256=None,
            pipeline_version="document-v1",
            max_attempts=5,
        )
        assert queued is not None and queued[1].status == "queued"
        claimed = await processing.claim_next(worker_id="worker-a", lease_seconds=60)
        assert claimed is not None and claimed[0].attempts == 1
        assert await processing.set_detected_type(
            job_id=claimed[0].id,
            worker_id="worker-a",
            detected_mime_type="text/plain",
            processor="plain_text:1",
        )
        assert await processing.complete_job(
            job_id=claimed[0].id,
            worker_id="worker-a",
            derivatives=[
                PendingDerivative(
                    id=str(uuid.uuid4()),
                    kind="extracted_text",
                    object_key=f"principals/{principal_id}/files/{file_id}/derivatives/text",
                    mime_type="text/plain",
                    size_bytes=1,
                )
            ],
            processing_metadata={"characterCount": 1},
        )
        ready = await catalog.get_file(file_id=str(file_id), principal_id=str(principal_id))
        assert ready is not None and ready.status == "ready"
        assert ready.detected_mime_type == "text/plain"
        derivatives = await processing.list_derivatives(file_id=str(file_id))
        assert [row.kind for row in derivatives] == ["extracted_text"]
    finally:
        async with factory() as db, db.begin():
            await db.execute(delete(File).where(File.id == file_id))
            await db.execute(delete(Principal).where(Principal.id == principal_id))
        await engine.dispose()
