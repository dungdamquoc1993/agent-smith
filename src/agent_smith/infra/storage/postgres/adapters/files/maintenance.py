"""Postgres adapter for bounded file-maintenance workflows."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.files import FileRecord
from agent_smith.infra.storage.postgres.adapters.files.records import file_record
from agent_smith.infra.storage.postgres.models.file_audit import FileAuditEvent
from agent_smith.infra.storage.postgres.models.file_processing import FileProcessingJob
from agent_smith.infra.storage.postgres.models.files import File, FileStatus
from agent_smith.infra.storage.postgres.models.sessions import SessionEntryFile


class PostgresFileMaintenanceStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def mark_expired_upload(
        self, *, file_id: str, principal_id: str
    ) -> FileRecord | None:
        async with self._session_factory() as db, db.begin():
            row = await db.scalar(
                update(File)
                .where(
                    File.id == _uuid(file_id),
                    File.principal_id == _uuid(principal_id),
                    File.status == FileStatus.pending_upload,
                )
                .values(
                    status=FileStatus.failed,
                    failure_reason="upload_expired",
                    updated_at=datetime.now(UTC),
                )
                .returning(File)
            )
            return file_record(row) if row is not None else None

    async def list_stale_pending(
        self, *, created_before: datetime, limit: int
    ) -> list[FileRecord]:
        return await self._list(
            File.status == FileStatus.pending_upload,
            File.created_at < created_before,
            limit=limit,
        )

    async def list_deleted(
        self, *, deleted_before: datetime, limit: int
    ) -> list[FileRecord]:
        return await self._list(
            File.status == FileStatus.deleted,
            File.deleted_at < deleted_before,
            or_(
                File.object_deleted_at.is_(None),
                ~exists().where(SessionEntryFile.file_id == File.id),
            ),
            limit=limit,
        )

    async def list_rejected_objects(self, *, limit: int) -> list[FileRecord]:
        return await self._list(
            File.status == FileStatus.failed,
            File.failure_reason.in_(
                ("size_mismatch", "checksum_mismatch", "mime_mismatch", "upload_expired")
            ),
            File.object_deleted_at.is_(None),
            ~exists().where(FileProcessingJob.file_id == File.id),
            limit=limit,
        )

    async def mark_object_deleted(
        self, *, file_id: str, deleted_at: datetime
    ) -> FileRecord | None:
        async with self._session_factory() as db, db.begin():
            row = await db.scalar(
                update(File)
                .where(
                    File.id == _uuid(file_id),
                    File.status.in_((FileStatus.failed, FileStatus.deleted)),
                    File.object_deleted_at.is_(None),
                )
                .values(object_deleted_at=deleted_at, updated_at=datetime.now(UTC))
                .returning(File)
            )
            return file_record(row) if row is not None else None

    async def purge_file(self, *, file_id: str) -> bool:
        async with self._session_factory() as db, db.begin():
            result = await db.execute(
                delete(File).where(
                    File.id == _uuid(file_id),
                    File.status == FileStatus.deleted,
                    File.object_deleted_at.is_not(None),
                    ~exists().where(SessionEntryFile.file_id == File.id),
                )
            )
            return bool(result.rowcount)

    async def purge_audit_events_before(
        self, *, occurred_before: datetime, limit: int
    ) -> int:
        if limit < 1:
            return 0
        async with self._session_factory() as db, db.begin():
            ids = (
                await db.scalars(
                    select(FileAuditEvent.id)
                    .where(FileAuditEvent.occurred_at < occurred_before)
                    .order_by(FileAuditEvent.occurred_at)
                    .limit(limit)
                )
            ).all()
            if not ids:
                return 0
            result = await db.execute(delete(FileAuditEvent).where(FileAuditEvent.id.in_(ids)))
            return int(result.rowcount or 0)

    async def _list(self, *conditions: object, limit: int) -> list[FileRecord]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(File).where(*conditions).order_by(File.created_at).limit(limit)
                )
            ).all()
            return [file_record(row) for row in rows]


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Invalid UUID") from exc
