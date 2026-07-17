from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from agent_smith.app.ports.files import (
    BlobObjectStat,
    BlobStorageError,
    FileAuditEvent,
    FileAuditUnavailable,
    FileCursor,
    FileRecord,
    FileStatus,
    FileQuotaExceeded,
    PendingFileRecord,
    PresignedRequest,
    TooManyPendingUploads,
)
from agent_smith.app.ports.document_processing import (
    DerivativeRecord,
    ProcessingJobRecord,
)


class FakeFileCatalog:
    def __init__(self) -> None:
        self.records: dict[str, FileRecord] = {}
        self.referenced_file_ids: set[str] = set()
        self.processing_file_ids: set[str] = set()
        self.audit_events: list[FileAuditEvent] = []
        self.fail_audit = False

    async def create_pending(
        self,
        file: PendingFileRecord,
        *,
        quota_bytes: int | None = None,
        max_pending_uploads: int | None = None,
        audit: FileAuditEvent | None = None,
    ) -> FileRecord:
        self._check_audit(audit)
        principal_rows = [
            row
            for row in self.records.values()
            if row.principal_id == file.principal_id
        ]
        if max_pending_uploads is not None and sum(
            row.status == "pending_upload" for row in principal_rows
        ) >= max_pending_uploads:
            raise TooManyPendingUploads
        usage = sum(
            row.size_bytes for row in principal_rows if row.object_deleted_at is None
        )
        if quota_bytes is not None and usage + file.size_bytes > quota_bytes:
            raise FileQuotaExceeded
        now = datetime.now(UTC)
        record = FileRecord(
            **file.__dict__,
            status="pending_upload",
            created_at=now,
            updated_at=now,
        )
        self.records[file.id] = record
        self._append_audit(audit)
        return record

    async def get_file(
        self,
        *,
        file_id: str,
        principal_id: str,
        include_deleted: bool = False,
    ) -> FileRecord | None:
        record = self.records.get(file_id)
        if record is None or record.principal_id != principal_id:
            return None
        if record.status == "deleted" and not include_deleted:
            return None
        return record

    async def list_files(
        self,
        *,
        principal_id: str,
        limit: int,
        cursor: FileCursor | None = None,
        status: FileStatus | None = None,
        mime_type: str | None = None,
    ) -> list[FileRecord]:
        rows = [
            row
            for row in self.records.values()
            if row.principal_id == principal_id
            and row.status != "deleted"
            and (status is None or row.status == status)
            and (mime_type is None or row.mime_type == mime_type)
        ]
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        if cursor:
            rows = [
                row
                for row in rows
                if row.created_at is not None
                and (row.created_at, row.id) < (cursor.created_at, cursor.id)
            ]
        return rows[:limit]

    async def mark_uploaded(self, **values: object) -> FileRecord | None:
        status = str(values.get("final_status", "uploaded"))
        return self._transition(values, {"pending_upload"}, status)  # type: ignore[arg-type]

    async def mark_processing(self, **values: object) -> FileRecord | None:
        return self._transition(values, {"uploaded"}, "processing")

    async def mark_ready(self, **values: object) -> FileRecord | None:
        return self._transition(values, {"uploaded", "processing"}, "ready")

    async def mark_failed(self, **values: object) -> FileRecord | None:
        return self._transition(
            values,
            {"pending_upload"}
            if values.get("pending_only")
            else {"pending_upload", "uploaded", "processing"},
            "failed",
        )

    async def soft_delete(self, **values: object) -> FileRecord | None:
        return self._transition(
            values,
            {"pending_upload", "uploaded", "processing", "ready", "failed"},
            "deleted",
        )

    async def list_stale_pending(self, *, created_before: datetime, limit: int):
        return [
            row
            for row in self.records.values()
            if row.status == "pending_upload"
            and row.created_at is not None
            and row.created_at < created_before
        ][:limit]

    async def list_deleted(self, *, deleted_before: datetime, limit: int):
        return [
            row
            for row in self.records.values()
            if row.status == "deleted"
            and row.deleted_at is not None
            and row.deleted_at < deleted_before
            and (
                row.object_deleted_at is None
                or row.id not in self.referenced_file_ids
            )
        ][:limit]

    async def list_rejected_objects(self, *, limit: int):
        reasons = {"size_mismatch", "checksum_mismatch", "mime_mismatch", "upload_expired"}
        return [
            row
            for row in self.records.values()
            if row.status == "failed"
            and row.failure_reason in reasons
            and row.object_deleted_at is None
            and row.id not in self.processing_file_ids
        ][:limit]

    async def purge_file(self, *, file_id: str) -> bool:
        record = self.records.get(file_id)
        if (
            record is None
            or record.status != "deleted"
            or record.object_deleted_at is None
            or file_id in self.referenced_file_ids
        ):
            return False
        del self.records[file_id]
        return True

    async def mark_object_deleted(
        self, *, file_id: str, deleted_at: datetime
    ) -> FileRecord | None:
        record = self.records.get(file_id)
        if (
            record is None
            or record.status not in {"failed", "deleted"}
            or record.object_deleted_at is not None
        ):
            return None
        updated = replace(record, object_deleted_at=deleted_at, updated_at=datetime.now(UTC))
        self.records[file_id] = updated
        return updated

    def _transition(
        self,
        values: dict[str, object],
        allowed: set[str],
        status: FileStatus,
    ) -> FileRecord | None:
        audit = values.get("audit")
        self._check_audit(audit if isinstance(audit, FileAuditEvent) else None)
        file_id = str(values["file_id"])
        record = self.records.get(file_id)
        if (
            record is None
            or record.principal_id != values["principal_id"]
            or record.status not in allowed
        ):
            return None
        changes = {"status": status, "updated_at": datetime.now(UTC)}
        for key in (
            "mime_type",
            "etag",
            "sha256",
            "reason",
            "deleted_at",
            "detected_mime_type",
            "processing_metadata",
        ):
            if key in values:
                changes["failure_reason" if key == "reason" else key] = values[key]
        updated = replace(record, **changes)
        self.records[file_id] = updated
        self._append_audit(audit if isinstance(audit, FileAuditEvent) else None)
        return updated

    async def append(self, events: list[FileAuditEvent]) -> None:
        if self.fail_audit:
            raise FileAuditUnavailable("fake audit failure")
        now = datetime.now(UTC)
        self.audit_events.extend(
            replace(event, occurred_at=event.occurred_at or now) for event in events
        )

    async def purge_before(self, *, occurred_before: datetime, limit: int) -> int:
        old = [
            event
            for event in self.audit_events
            if event.occurred_at is not None and event.occurred_at < occurred_before
        ][:limit]
        for event in old:
            self.audit_events.remove(event)
        return len(old)

    def _check_audit(self, audit: FileAuditEvent | None) -> None:
        if audit is not None and self.fail_audit:
            raise FileAuditUnavailable("fake audit failure")

    def _append_audit(self, audit: FileAuditEvent | None) -> None:
        if audit is not None:
            self.audit_events.append(
                replace(audit, occurred_at=audit.occurred_at or datetime.now(UTC))
            )


class FakeBlobStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str | None]] = {}
        self.fail = False
        self.fail_delete = False
        self.deleted: list[str] = []

    async def create_upload_url(self, **values: object) -> PresignedRequest:
        self._check()
        return PresignedRequest(
            url=f"https://storage.test/{values['object_key']}?signed=upload",
            method="PUT",
            expires_at=datetime.now(UTC),
            headers={"Content-Type": str(values["mime_type"])},
        )

    async def create_download_url(self, **values: object) -> PresignedRequest:
        self._check()
        return PresignedRequest(
            url=f"https://storage.test/{values['object_key']}?signed=download",
            method="GET",
            expires_at=datetime.now(UTC),
        )

    async def stat(self, *, object_key: str) -> BlobObjectStat | None:
        self._check()
        value = self.objects.get(object_key)
        if value is None:
            return None
        data, mime_type, sha256 = value
        return BlobObjectStat(
            size_bytes=len(data),
            etag="fake-etag",
            content_type=mime_type,
            checksum_sha256=sha256,
        )

    async def read_range(self, *, object_key: str, start: int, end: int) -> bytes:
        self._check()
        return self.objects[object_key][0][start : end + 1]

    async def read_object(self, *, object_key: str, max_bytes: int) -> bytes:
        self._check()
        data = self.objects[object_key][0]
        if len(data) > max_bytes:
            raise BlobStorageError("fake object exceeds bounded read")
        return data

    async def delete(self, *, object_key: str) -> None:
        self._check()
        if self.fail_delete:
            raise BlobStorageError("fake delete failure")
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)

    async def write_object(
        self, *, object_key: str, data: bytes, mime_type: str
    ) -> BlobObjectStat:
        self._check()
        self.objects[object_key] = (data, mime_type, None)
        return BlobObjectStat(size_bytes=len(data), content_type=mime_type)

    async def delete_prefix(self, *, prefix: str) -> None:
        self._check()
        if self.fail_delete:
            raise BlobStorageError("fake delete failure")
        for key in [key for key in self.objects if key.startswith(prefix)]:
            self.deleted.append(key)
            del self.objects[key]

    def upload(self, record: FileRecord, data: bytes, *, sha256: str | None = None) -> None:
        self.objects[record.object_key] = (data, record.mime_type, sha256)

    def _check(self) -> None:
        if self.fail:
            raise BlobStorageError("fake storage failure")


class FakeFileProcessingStore:
    def __init__(self, catalog: FakeFileCatalog) -> None:
        self.catalog = catalog
        self.jobs: dict[str, ProcessingJobRecord] = {}
        self.derivatives: dict[str, list[DerivativeRecord]] = {}

    async def mark_uploaded_and_enqueue(self, **values: object):
        record = self.catalog._transition(values, {"pending_upload"}, "uploaded")
        if record is None:
            return None
        now = datetime.now(UTC)
        job = ProcessingJobRecord(
            id=f"job-{record.id}",
            file_id=record.id,
            pipeline_version=str(values["pipeline_version"]),
            status="queued",
            attempts=0,
            max_attempts=int(values["max_attempts"]),
            created_at=now,
            updated_at=now,
        )
        self.jobs[record.id] = job
        self.catalog.processing_file_ids.add(record.id)
        return record, job

    async def get_latest_jobs(self, *, file_ids: list[str]):
        return {file_id: self.jobs[file_id] for file_id in file_ids if file_id in self.jobs}

    async def list_derivatives(self, *, file_id: str, kinds=None):
        rows = self.derivatives.get(file_id, [])
        return [row for row in rows if not kinds or row.kind in kinds]

    async def claim_next(self, *, worker_id: str, lease_seconds: int):
        del lease_seconds
        for file_id, job in list(self.jobs.items()):
            if job.status not in {"queued", "retry_wait"}:
                continue
            now = datetime.now(UTC)
            if job.available_at is not None and job.available_at > now:
                continue
            running = ProcessingJobRecord(
                **{
                    **job.__dict__,
                    "status": "running",
                    "attempts": job.attempts + 1,
                    "phase": "downloading",
                    "progress_percent": 5,
                    "lease_owner": worker_id,
                    "updated_at": now,
                }
            )
            self.jobs[file_id] = running
            record = self.catalog.records[file_id]
            record = replace(record, status="processing", updated_at=now)
            self.catalog.records[file_id] = record
            return running, record
        return None

    async def heartbeat(self, **values: object) -> bool:
        return any(job.id == values["job_id"] for job in self.jobs.values())

    async def set_detected_type(self, **values: object) -> bool:
        for file_id, job in self.jobs.items():
            if job.id == values["job_id"] and job.lease_owner == values["worker_id"]:
                self.jobs[file_id] = ProcessingJobRecord(
                    **{**job.__dict__, "processor": values["processor"]}
                )
                record = self.catalog.records[file_id]
                self.catalog.records[file_id] = replace(
                    record, detected_mime_type=str(values["detected_mime_type"])
                )
                return True
        return False

    async def update_progress(self, **values: object) -> bool:
        for file_id, job in self.jobs.items():
            if job.id == values["job_id"] and job.lease_owner == values["worker_id"]:
                self.jobs[file_id] = ProcessingJobRecord(
                    **{
                        **job.__dict__,
                        "phase": values["phase"],
                        "progress_percent": int(values["progress_percent"]),
                    }
                )
                return True
        return False

    async def complete_job(self, **values: object) -> bool:
        for file_id, job in self.jobs.items():
            if job.id != values["job_id"] or job.lease_owner != values["worker_id"]:
                continue
            now = datetime.now(UTC)
            self.jobs[file_id] = ProcessingJobRecord(
                **{
                    **job.__dict__,
                    "status": "succeeded",
                    "phase": "completed",
                    "progress_percent": 100,
                    "lease_owner": None,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
            self.catalog.records[file_id] = replace(
                self.catalog.records[file_id],
                status="ready",
                processing_metadata=dict(values["processing_metadata"]),
                updated_at=now,
            )
            self.derivatives[file_id] = [
                DerivativeRecord(
                    id=row.id,
                    file_id=file_id,
                    processing_job_id=job.id,
                    kind=row.kind,
                    object_key=row.object_key,
                    mime_type=row.mime_type,
                    size_bytes=row.size_bytes,
                    metadata=row.metadata,
                )
                for row in values["derivatives"]
            ]
            return True
        return False

    async def fail_job(self, **values: object) -> bool:
        return self._finish_error(values, retry=False)

    async def schedule_retry(self, **values: object) -> bool:
        return self._finish_error(values, retry=True)

    async def cancel_jobs(self, *, file_id: str) -> None:
        job = self.jobs.get(file_id)
        if job:
            self.jobs[file_id] = ProcessingJobRecord(
                **{**job.__dict__, "status": "cancelled", "phase": "cancelled"}
            )

    async def reconcile_uploaded(self, **_values: object) -> int:
        return 0

    def add_derivative(
        self,
        blobs: FakeBlobStore,
        record: FileRecord,
        *,
        kind: str,
        data: bytes,
        mime_type: str,
    ) -> None:
        object_key = f"{record.object_key}/derivatives/{kind}"
        blobs.objects[object_key] = (data, mime_type, None)
        row = DerivativeRecord(
            id=f"{record.id}-{kind}",
            file_id=record.id,
            processing_job_id=f"job-{record.id}",
            kind=kind,
            object_key=object_key,
            mime_type=mime_type,
            size_bytes=len(data),
        )
        self.derivatives.setdefault(record.id, []).append(row)

    def _finish_error(self, values: dict[str, object], *, retry: bool) -> bool:
        for file_id, job in self.jobs.items():
            if job.id != values["job_id"]:
                continue
            status = "retry_wait" if retry and job.attempts < job.max_attempts else "failed"
            self.jobs[file_id] = ProcessingJobRecord(
                **{
                    **job.__dict__,
                    "status": status,
                    "phase": status,
                    "error": dict(values["error"]),
                    "available_at": values.get("available_at", job.available_at),
                    "lease_owner": None,
                }
            )
            if status == "failed":
                self.catalog.records[file_id] = replace(
                    self.catalog.records[file_id],
                    status="failed",
                    failure_reason=str(values["error"].get("code")),
                )
            return True
        return False
