from __future__ import annotations

import asyncio
import uuid
from os import getenv

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.app.ports.files import (
    FileAuditEvent as AuditEvent,
    FileAuditUnavailable,
    FileQuotaExceeded,
    PendingFileRecord,
    TooManyPendingUploads,
)
from agent_smith.infra.storage.postgres.adapters import PostgresFileCatalog
from agent_smith.infra.storage.postgres.database import Base
from agent_smith.infra.storage.postgres.models.file_audit import FileAuditEvent
from agent_smith.infra.storage.postgres.models.files import File
from agent_smith.infra.storage.postgres.models.principals import Principal


@pytest.mark.asyncio
async def test_concurrent_quota_and_pending_reservations_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = PostgresFileCatalog(factory)
    quota_principal = uuid.uuid4()
    pending_principal = uuid.uuid4()
    actor_subject = f"hardening-{uuid.uuid4()}"
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add_all(
                [
                    Principal(id=quota_principal, display_name="Quota Principal"),
                    Principal(id=pending_principal, display_name="Pending Principal"),
                ]
            )

        quota_results = await asyncio.gather(
            *[
                catalog.create_pending(
                    _pending(quota_principal, size_bytes=6),
                    quota_bytes=10,
                    max_pending_uploads=10,
                    audit=_audit(quota_principal, actor_subject),
                )
                for _ in range(2)
            ],
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in quota_results) == 1
        assert sum(isinstance(result, FileQuotaExceeded) for result in quota_results) == 1

        pending_results = await asyncio.gather(
            *[
                catalog.create_pending(
                    _pending(pending_principal, size_bytes=1),
                    quota_bytes=100,
                    max_pending_uploads=1,
                    audit=_audit(pending_principal, actor_subject),
                )
                for _ in range(2)
            ],
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in pending_results) == 1
        assert sum(isinstance(result, TooManyPendingUploads) for result in pending_results) == 1
    finally:
        async with factory() as db, db.begin():
            await db.execute(
                delete(FileAuditEvent).where(FileAuditEvent.actor_subject == actor_subject)
            )
            await db.execute(
                delete(File).where(File.principal_id.in_([quota_principal, pending_principal]))
            )
            await db.execute(
                delete(Principal).where(Principal.id.in_([quota_principal, pending_principal]))
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_file_mutation_and_audit_commit_or_rollback_together_when_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = PostgresFileCatalog(factory)
    principal_id = uuid.uuid4()
    actor_subject = f"hardening-{uuid.uuid4()}"
    pending = _pending(principal_id, size_bytes=1)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add(Principal(id=principal_id, display_name="Audit Principal"))
        record = await catalog.create_pending(pending)

        invalid_audit = _audit(principal_id, actor_subject, file_id=record.id)
        invalid_audit = AuditEvent(
            **{
                **invalid_audit.__dict__,
                "identity_provider_id": str(uuid.uuid4()),
            }
        )
        with pytest.raises(FileAuditUnavailable):
            await catalog.mark_failed(
                file_id=record.id,
                principal_id=str(principal_id),
                reason="size_mismatch",
                audit=invalid_audit,
            )
        unchanged = await catalog.get_file(
            file_id=record.id,
            principal_id=str(principal_id),
        )
        assert unchanged is not None and unchanged.status == "pending_upload"

        committed = await catalog.mark_failed(
            file_id=record.id,
            principal_id=str(principal_id),
            reason="size_mismatch",
            audit=_audit(principal_id, actor_subject, file_id=record.id),
        )
        assert committed is not None and committed.status == "failed"
        async with factory() as db:
            events = (
                await db.scalars(
                    select(FileAuditEvent).where(
                        FileAuditEvent.actor_subject == actor_subject
                    )
                )
            ).all()
            assert len(events) == 1

        async with factory() as db, db.begin():
            await db.execute(delete(File).where(File.id == uuid.UUID(record.id)))
        async with factory() as db:
            event = await db.scalar(
                select(FileAuditEvent).where(FileAuditEvent.actor_subject == actor_subject)
            )
            assert event is not None and event.file_id == uuid.UUID(record.id)

        async with factory() as db, db.begin():
            await db.execute(delete(Principal).where(Principal.id == principal_id))
        async with factory() as db:
            event = await db.scalar(
                select(FileAuditEvent).where(FileAuditEvent.actor_subject == actor_subject)
            )
            assert event is not None and event.principal_id is None
    finally:
        async with factory() as db, db.begin():
            await db.execute(
                delete(FileAuditEvent).where(FileAuditEvent.actor_subject == actor_subject)
            )
            await db.execute(delete(File).where(File.principal_id == principal_id))
            await db.execute(delete(Principal).where(Principal.id == principal_id))
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejected_cleanup_selection_excludes_processing_failure_when_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = PostgresFileCatalog(factory)
    principal_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add(Principal(id=principal_id, display_name="Cleanup Principal"))
        rejected = await catalog.create_pending(_pending(principal_id, size_bytes=1))
        processing = await catalog.create_pending(_pending(principal_id, size_bytes=1))
        await catalog.mark_failed(
            file_id=rejected.id,
            principal_id=str(principal_id),
            reason="mime_mismatch",
        )
        await catalog.mark_failed(
            file_id=processing.id,
            principal_id=str(principal_id),
            reason="corrupt_document",
        )

        selected = await catalog.list_rejected_objects(limit=100)
        assert [row.id for row in selected if row.principal_id == str(principal_id)] == [
            rejected.id
        ]
    finally:
        async with factory() as db, db.begin():
            await db.execute(delete(File).where(File.principal_id == principal_id))
            await db.execute(delete(Principal).where(Principal.id == principal_id))
        await engine.dispose()


def _pending(principal_id: uuid.UUID, *, size_bytes: int) -> PendingFileRecord:
    file_id = uuid.uuid4()
    return PendingFileRecord(
        id=str(file_id),
        principal_id=str(principal_id),
        original_name="test.txt",
        mime_type="text/plain",
        size_bytes=size_bytes,
        object_key=f"principals/{principal_id}/files/{file_id}/original",
    )


def _audit(
    principal_id: uuid.UUID,
    actor_subject: str,
    *,
    file_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        principal_id=str(principal_id),
        identity_provider_id=None,
        actor_subject=actor_subject,
        file_id=file_id,
        action="file.upload_initiated" if file_id is None else "file.upload_completed",
        outcome="succeeded",
        details={
            "mimeType": "text/plain",
            "declaredSize": 1,
            "resultingStatus": "pending_upload" if file_id is None else "failed",
        },
    )
