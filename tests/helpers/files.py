from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from agent_smith.app.ports.files import (
    BlobObjectStat,
    BlobStorageError,
    FileCursor,
    FileRecord,
    FileStatus,
    PendingFileRecord,
    PresignedRequest,
)


class FakeFileCatalog:
    def __init__(self) -> None:
        self.records: dict[str, FileRecord] = {}

    async def create_pending(self, file: PendingFileRecord) -> FileRecord:
        now = datetime.now(UTC)
        record = FileRecord(
            **file.__dict__,
            status="pending_upload",
            created_at=now,
            updated_at=now,
        )
        self.records[file.id] = record
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
        return self._transition(values, {"pending_upload"}, "uploaded")

    async def mark_processing(self, **values: object) -> FileRecord | None:
        return self._transition(values, {"uploaded"}, "processing")

    async def mark_ready(self, **values: object) -> FileRecord | None:
        return self._transition(values, {"processing"}, "ready")

    async def mark_failed(self, **values: object) -> FileRecord | None:
        return self._transition(
            values,
            {"pending_upload", "uploaded", "processing"},
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
        ][:limit]

    async def purge_file(self, *, file_id: str) -> bool:
        record = self.records.get(file_id)
        if record is None or record.status != "deleted":
            return False
        del self.records[file_id]
        return True

    def _transition(
        self,
        values: dict[str, object],
        allowed: set[str],
        status: FileStatus,
    ) -> FileRecord | None:
        file_id = str(values["file_id"])
        record = self.records.get(file_id)
        if (
            record is None
            or record.principal_id != values["principal_id"]
            or record.status not in allowed
        ):
            return None
        changes = {"status": status, "updated_at": datetime.now(UTC)}
        for key in ("mime_type", "etag", "sha256", "reason", "deleted_at"):
            if key in values:
                changes["failure_reason" if key == "reason" else key] = values[key]
        updated = replace(record, **changes)
        self.records[file_id] = updated
        return updated


class FakeBlobStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, str | None]] = {}
        self.fail = False
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

    async def delete(self, *, object_key: str) -> None:
        self._check()
        self.deleted.append(object_key)
        self.objects.pop(object_key, None)

    def upload(self, record: FileRecord, data: bytes, *, sha256: str | None = None) -> None:
        self.objects[record.object_key] = (data, record.mime_type, sha256)

    def _check(self) -> None:
        if self.fail:
            raise BlobStorageError("fake storage failure")
