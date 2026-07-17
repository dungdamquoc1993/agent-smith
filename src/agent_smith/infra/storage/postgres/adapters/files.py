"""Postgres implementation of the managed-file catalog."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.files import (
    FileAuditEvent,
    FileAuditUnavailable,
    FileCursor,
    FileQuotaExceeded,
    FileRecord,
    FileStatus as AppFileStatus,
    PendingFileRecord,
    TooManyPendingUploads,
)
from agent_smith.infra.storage.postgres.adapters.file_audit import add_audit_event
from agent_smith.infra.storage.postgres.models.file import File, FileProcessingJob, FileStatus
from agent_smith.infra.storage.postgres.models.principal import Principal
from agent_smith.infra.storage.postgres.models.session import SessionEntryFile


class PostgresFileCatalog:
    """Persist file metadata without leaking SQLAlchemy into the app layer."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_pending(
        self,
        file: PendingFileRecord,
        *,
        quota_bytes: int | None = None,
        max_pending_uploads: int | None = None,
        audit: FileAuditEvent | None = None,
    ) -> FileRecord:
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
                if quota_bytes is not None or max_pending_uploads is not None:
                    principal_id = uuid.UUID(file.principal_id)
                    locked = await db.scalar(
                        select(Principal.id)
                        .where(Principal.id == principal_id)
                        .with_for_update()
                    )
                    if locked is None:
                        raise ValueError("Principal does not exist")
                    if max_pending_uploads is not None:
                        pending_count = await db.scalar(
                            select(func.count())
                            .select_from(File)
                            .where(
                                File.principal_id == principal_id,
                                File.status == FileStatus.pending_upload,
                            )
                        )
                        if int(pending_count or 0) >= max_pending_uploads:
                            raise TooManyPendingUploads
                    if quota_bytes is not None:
                        usage = await db.scalar(
                            select(func.coalesce(func.sum(File.size_bytes), 0)).where(
                                File.principal_id == principal_id,
                                File.object_deleted_at.is_(None),
                            )
                        )
                        if int(usage or 0) + file.size_bytes > quota_bytes:
                            raise FileQuotaExceeded
                db.add(row)
                if audit is not None:
                    add_audit_event(db, audit)
                await db.flush()
                await db.refresh(row)
                return _record(row)
        except IntegrityError as exc:
            if audit is not None:
                raise FileAuditUnavailable(
                    "Unable to persist required file audit event"
                ) from exc
            raise ValueError("File id or object key already exists") from exc
        except (SQLAlchemyError, ValueError) as exc:
            if audit is not None:
                raise FileAuditUnavailable(
                    "Unable to persist required file audit event"
                ) from exc
            raise

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
        detected_mime_type: str | None = None,
        processing_metadata: dict[str, object] | None = None,
        final_status: str = "uploaded",
        audit: FileAuditEvent | None = None,
    ) -> FileRecord | None:
        values: dict[str, object] = {
            "mime_type": mime_type,
            "etag": etag,
            "sha256": sha256,
        }
        if detected_mime_type is not None:
            values["detected_mime_type"] = detected_mime_type
        if processing_metadata is not None:
            values["processing_metadata"] = processing_metadata
        if final_status not in {"uploaded", "ready"}:
            raise ValueError("Invalid upload completion status")
        return await self._transition(
            file_id=file_id,
            principal_id=principal_id,
            from_statuses=(FileStatus.pending_upload,),
            to_status=FileStatus(final_status),
            values=values,
            audit=audit,
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
            from_statuses=(FileStatus.uploaded, FileStatus.processing),
            to_status=FileStatus.ready,
        )

    async def mark_failed(
        self,
        *,
        file_id: str,
        principal_id: str,
        reason: str,
        pending_only: bool = False,
        audit: FileAuditEvent | None = None,
    ) -> FileRecord | None:
        return await self._transition(
            file_id=file_id,
            principal_id=principal_id,
            from_statuses=(FileStatus.pending_upload,)
            if pending_only
            else (
                FileStatus.pending_upload,
                FileStatus.uploaded,
                FileStatus.processing,
            ),
            to_status=FileStatus.failed,
            values={"failure_reason": reason[:4000]},
            audit=audit,
        )

    async def soft_delete(
        self,
        *,
        file_id: str,
        principal_id: str,
        deleted_at: datetime,
        audit: FileAuditEvent | None = None,
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
            audit=audit,
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
            or_(
                File.object_deleted_at.is_(None),
                ~exists().where(SessionEntryFile.file_id == File.id),
            ),
            limit=limit,
        )

    async def list_rejected_objects(self, *, limit: int) -> list[FileRecord]:
        return await self._list_for_cleanup(
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
            return _record(row) if row is not None else None

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

    async def _transition(
        self,
        *,
        file_id: str,
        principal_id: str,
        from_statuses: tuple[FileStatus, ...],
        to_status: FileStatus,
        values: dict[str, object] | None = None,
        audit: FileAuditEvent | None = None,
    ) -> FileRecord | None:
        changes = {"status": to_status, "updated_at": datetime.now(UTC)}
        changes.update(values or {})
        try:
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
                if row is not None and audit is not None:
                    add_audit_event(db, audit)
                    await db.flush()
                return _record(row) if row is not None else None
        except (SQLAlchemyError, ValueError) as exc:
            if audit is not None:
                raise FileAuditUnavailable(
                    "Unable to persist required file audit event"
                ) from exc
            raise

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
        detected_mime_type=row.detected_mime_type,
        processing_metadata=dict(row.processing_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
        object_deleted_at=row.object_deleted_at,
    )
