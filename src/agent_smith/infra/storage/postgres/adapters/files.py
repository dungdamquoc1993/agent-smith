"""Postgres implementation of the managed-file catalog."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.files import (
    FileCursor,
    FileRecord,
    FileStatus as AppFileStatus,
    PendingFileRecord,
)
from agent_smith.infra.storage.postgres.models.file import File, FileStatus


class PostgresFileCatalog:
    """Persist file metadata without leaking SQLAlchemy into the app layer."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_pending(self, file: PendingFileRecord) -> FileRecord:
        row = File(
            id=uuid.UUID(file.id),
            principal_id=uuid.UUID(file.principal_id),
            original_name=file.original_name,
            mime_type=file.mime_type,
            size_bytes=file.size_bytes,
            sha256=file.sha256,
            object_key=file.object_key,
            status=FileStatus.pending_upload,
            file_metadata=file.metadata,
        )
        try:
            async with self._session_factory() as db, db.begin():
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return _record(row)
        except IntegrityError as exc:
            raise ValueError("File id or object key already exists") from exc

    async def get_file(
        self,
        *,
        file_id: str,
        principal_id: str,
        include_deleted: bool = False,
    ) -> FileRecord | None:
        conditions = [
            File.id == _uuid(file_id),
            File.principal_id == _uuid(principal_id),
        ]
        if not include_deleted:
            conditions.append(File.status != FileStatus.deleted)
        async with self._session_factory() as db:
            row = await db.scalar(select(File).where(*conditions))
            return _record(row) if row is not None else None

    async def list_files(
        self,
        *,
        principal_id: str,
        limit: int,
        cursor: FileCursor | None = None,
        status: AppFileStatus | None = None,
        mime_type: str | None = None,
    ) -> list[FileRecord]:
        conditions = [
            File.principal_id == _uuid(principal_id),
            File.status != FileStatus.deleted,
        ]
        if status is not None:
            conditions.append(File.status == FileStatus(status))
        if mime_type is not None:
            conditions.append(File.mime_type == mime_type)
        if cursor is not None:
            conditions.append(
                or_(
                    File.created_at < cursor.created_at,
                    and_(File.created_at == cursor.created_at, File.id < _uuid(cursor.id)),
                )
            )
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(File)
                    .where(*conditions)
                    .order_by(File.created_at.desc(), File.id.desc())
                    .limit(limit)
                )
            ).all()
            return [_record(row) for row in rows]

    async def mark_uploaded(
        self,
        *,
        file_id: str,
        principal_id: str,
        mime_type: str,
        etag: str | None,
        sha256: str | None,
    ) -> FileRecord | None:
        return await self._transition(
            file_id=file_id,
            principal_id=principal_id,
            from_statuses=(FileStatus.pending_upload,),
            to_status=FileStatus.uploaded,
            values={"mime_type": mime_type, "etag": etag, "sha256": sha256},
        )

    async def mark_processing(self, *, file_id: str, principal_id: str) -> FileRecord | None:
        return await self._transition(
            file_id=file_id,
            principal_id=principal_id,
            from_statuses=(FileStatus.uploaded,),
            to_status=FileStatus.processing,
        )

    async def mark_ready(self, *, file_id: str, principal_id: str) -> FileRecord | None:
        return await self._transition(
            file_id=file_id,
            principal_id=principal_id,
            from_statuses=(FileStatus.processing,),
            to_status=FileStatus.ready,
        )

    async def mark_failed(
        self,
        *,
        file_id: str,
        principal_id: str,
        reason: str,
    ) -> FileRecord | None:
        return await self._transition(
            file_id=file_id,
            principal_id=principal_id,
            from_statuses=(
                FileStatus.pending_upload,
                FileStatus.uploaded,
                FileStatus.processing,
            ),
            to_status=FileStatus.failed,
            values={"failure_reason": reason[:4000]},
        )

    async def soft_delete(
        self,
        *,
        file_id: str,
        principal_id: str,
        deleted_at: datetime,
    ) -> FileRecord | None:
        return await self._transition(
            file_id=file_id,
            principal_id=principal_id,
            from_statuses=(
                FileStatus.pending_upload,
                FileStatus.uploaded,
                FileStatus.processing,
                FileStatus.ready,
                FileStatus.failed,
            ),
            to_status=FileStatus.deleted,
            values={"deleted_at": deleted_at},
        )

    async def list_stale_pending(
        self,
        *,
        created_before: datetime,
        limit: int,
    ) -> list[FileRecord]:
        return await self._list_for_cleanup(
            File.status == FileStatus.pending_upload,
            File.created_at < created_before,
            limit=limit,
        )

    async def list_deleted(
        self,
        *,
        deleted_before: datetime,
        limit: int,
    ) -> list[FileRecord]:
        return await self._list_for_cleanup(
            File.status == FileStatus.deleted,
            File.deleted_at < deleted_before,
            limit=limit,
        )

    async def purge_file(self, *, file_id: str) -> bool:
        async with self._session_factory() as db, db.begin():
            result = await db.execute(
                delete(File).where(
                    File.id == _uuid(file_id),
                    File.status == FileStatus.deleted,
                )
            )
            return bool(result.rowcount)

    async def _transition(
        self,
        *,
        file_id: str,
        principal_id: str,
        from_statuses: tuple[FileStatus, ...],
        to_status: FileStatus,
        values: dict[str, object] | None = None,
    ) -> FileRecord | None:
        changes = {"status": to_status, "updated_at": datetime.now(UTC)}
        changes.update(values or {})
        async with self._session_factory() as db, db.begin():
            row = await db.scalar(
                update(File)
                .where(
                    File.id == _uuid(file_id),
                    File.principal_id == _uuid(principal_id),
                    File.status.in_(from_statuses),
                )
                .values(**changes)
                .returning(File)
            )
            return _record(row) if row is not None else None

    async def _list_for_cleanup(self, *conditions: object, limit: int) -> list[FileRecord]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(File).where(*conditions).order_by(File.created_at).limit(limit)
                )
            ).all()
            return [_record(row) for row in rows]


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Invalid UUID") from exc


def _record(row: File) -> FileRecord:
    return FileRecord(
        id=str(row.id),
        principal_id=str(row.principal_id),
        original_name=row.original_name,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        object_key=row.object_key,
        status=row.status.value,
        etag=row.etag,
        failure_reason=row.failure_reason,
        metadata=dict(row.file_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )
