from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agent_smith.app.ports.files import FileAuditActor, FileAuditEvent
from agent_smith.app.services.files import (
    FileService,
    FileServiceError,
    PrincipalRateLimiter,
)
from helpers.files import FakeBlobStore, FakeFileCatalog, FakeFileProcessingStore


def _service() -> tuple[FileService, FakeFileCatalog, FakeBlobStore]:
    catalog = FakeFileCatalog()
    blobs = FakeBlobStore()
    return (
        FileService(catalog, blobs, max_bytes=1024, presign_ttl_seconds=900),
        catalog,
        blobs,
    )


@pytest.mark.asyncio
async def test_initiate_complete_and_complete_again() -> None:
    service, _, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="notes.md",
        mime_type="text/markdown",
        size_bytes=5,
    )
    assert initiated.upload.method == "PUT"
    assert initiated.file.object_key.startswith("principals/principal-a/files/")
    assert "notes.md" not in initiated.file.object_key

    blobs.upload(initiated.file, b"hello")
    completed = await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)
    again = await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)

    assert completed.status == "uploaded"
    assert again == completed


@pytest.mark.asyncio
async def test_complete_image_moves_directly_to_ready() -> None:
    service, _, blobs = _service()
    data = b"\x89PNG\r\n\x1a\n"
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="photo.png",
        mime_type="image/png",
        size_bytes=len(data),
    )
    blobs.upload(initiated.file, data)

    completed = await service.complete_upload(
        principal_id="principal-a", file_id=initiated.file.id
    )

    assert completed.status == "ready"


@pytest.mark.asyncio
async def test_complete_document_atomically_enqueues_when_processing_store_is_configured() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    processing = FakeFileProcessingStore(catalog)
    service = FileService(
        catalog,
        blobs,
        max_bytes=1024,
        presign_ttl_seconds=900,
        processing_store=processing,
    )
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="notes.txt",
        mime_type="text/plain",
        size_bytes=5,
    )
    blobs.upload(initiated.file, b"hello")

    completed = await service.complete_upload(
        principal_id="principal-a", file_id=initiated.file.id
    )

    assert completed.status == "uploaded"
    assert processing.jobs[completed.id].status == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mime_type",
    ["application/msword", "application/vnd.ms-excel", "application/octet-stream"],
)
async def test_initiate_rejects_unsupported_and_legacy_types_with_415(mime_type: str) -> None:
    service, _, _ = _service()

    with pytest.raises(FileServiceError) as exc:
        await service.initiate_upload(
            principal_id="principal-a",
            original_name="legacy.bin",
            mime_type=mime_type,
            size_bytes=10,
        )

    assert exc.value.status == 415
    assert exc.value.code == "unsupported_file_type"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "error_message"),
    [
        (None, "not found"),
        (b"too long", "size does not match"),
        (b"\x00\x00\x00\x00\x00", "content type"),
    ],
)
async def test_complete_rejects_missing_size_or_mime_mismatch(
    data: bytes | None, error_message: str
) -> None:
    service, catalog, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="notes.md",
        mime_type="text/markdown",
        size_bytes=5,
    )
    if data is not None:
        blobs.upload(initiated.file, data)

    with pytest.raises(FileServiceError, match=error_message):
        await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)

    if data is not None:
        assert catalog.records[initiated.file.id].status == "failed"


@pytest.mark.asyncio
async def test_rejected_upload_is_deleted_and_cannot_be_downloaded() -> None:
    service, catalog, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="notes.txt",
        mime_type="text/plain",
        size_bytes=5,
    )
    blobs.upload(initiated.file, b"too long")

    with pytest.raises(FileServiceError):
        await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)

    rejected = catalog.records[initiated.file.id]
    assert rejected.status == "failed"
    assert rejected.object_deleted_at is not None
    assert rejected.object_key not in blobs.objects
    with pytest.raises(FileServiceError) as exc:
        await service.create_download_url(
            principal_id="principal-a", file_id=initiated.file.id
        )
    assert exc.value.code == "invalid_file_state"


@pytest.mark.asyncio
async def test_rejected_upload_stays_non_downloadable_while_delete_retries() -> None:
    service, catalog, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="notes.txt",
        mime_type="text/plain",
        size_bytes=5,
    )
    blobs.upload(initiated.file, b"too long")
    blobs.fail_delete = True
    with pytest.raises(FileServiceError):
        await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)

    rejected = catalog.records[initiated.file.id]
    assert rejected.failure_reason == "size_mismatch"
    assert rejected.object_deleted_at is None
    with pytest.raises(FileServiceError) as exc:
        await service.create_download_url(
            principal_id="principal-a", file_id=initiated.file.id
        )
    assert exc.value.code == "invalid_file_state"

    blobs.fail_delete = False
    assert await service.cleanup_rejected_uploads() == 1
    assert catalog.records[initiated.file.id].object_deleted_at is not None


@pytest.mark.asyncio
async def test_cross_principal_access_looks_not_found() -> None:
    service, _, _ = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="a.txt",
        mime_type="text/plain",
        size_bytes=1,
    )

    with pytest.raises(FileServiceError) as exc:
        await service.get_file(principal_id="principal-b", file_id=initiated.file.id)

    assert exc.value.status == 404
    assert exc.value.code == "file_not_found"


@pytest.mark.asyncio
async def test_list_paginates_and_filters() -> None:
    service, _, _ = _service()
    for name in ("a.txt", "b.txt", "c.md"):
        await service.initiate_upload(
            principal_id="principal-a",
            original_name=name,
            mime_type="text/markdown" if name.endswith(".md") else "text/plain",
            size_bytes=1,
        )

    first = await service.list_files(principal_id="principal-a", limit=2)
    second = await service.list_files(principal_id="principal-a", limit=2, cursor=first.next_cursor)
    markdown = await service.list_files(principal_id="principal-a", mime_type="text/markdown")

    assert len(first.files) == 2
    assert first.next_cursor
    assert len(second.files) == 1
    assert [file.original_name for file in markdown.files] == ["c.md"]


@pytest.mark.asyncio
async def test_deleted_file_cannot_create_download_url() -> None:
    service, _, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="a.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    blobs.upload(initiated.file, b"a")
    await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)
    await service.delete_file(principal_id="principal-a", file_id=initiated.file.id)

    with pytest.raises(FileServiceError) as exc:
        await service.create_download_url(principal_id="principal-a", file_id=initiated.file.id)
    assert exc.value.code == "file_not_found"


@pytest.mark.asyncio
async def test_failed_processing_keeps_original_downloadable() -> None:
    service, catalog, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="broken.pdf",
        mime_type="application/pdf",
        size_bytes=5,
    )
    blobs.upload(initiated.file, b"%PDF")
    failed = await catalog.mark_failed(
        file_id=initiated.file.id,
        principal_id="principal-a",
        reason="corrupt_document",
    )
    assert failed is not None

    download = await service.create_download_url(
        principal_id="principal-a", file_id=initiated.file.id
    )

    assert download.method == "GET"


@pytest.mark.asyncio
async def test_rejected_cleanup_does_not_delete_processing_mime_failure() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    processing = FakeFileProcessingStore(catalog)
    service = FileService(
        catalog,
        blobs,
        max_bytes=1024,
        presign_ttl_seconds=600,
        processing_store=processing,
    )
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="document.pdf",
        mime_type="application/pdf",
        size_bytes=5,
    )
    blobs.upload(initiated.file, b"%PDF-")
    await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)
    failed = await catalog.mark_failed(
        file_id=initiated.file.id,
        principal_id="principal-a",
        reason="mime_mismatch",
    )
    assert failed is not None

    assert await service.cleanup_rejected_uploads() == 0
    assert initiated.file.object_key in blobs.objects
    assert (
        await service.create_download_url(
            principal_id="principal-a", file_id=initiated.file.id
        )
    ).method == "GET"


@pytest.mark.asyncio
async def test_stale_pending_cleanup_is_idempotent() -> None:
    service, catalog, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="a.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    catalog.records[initiated.file.id] = replace(
        initiated.file,
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    blobs.upload(initiated.file, b"a")

    assert await service.cleanup_stale_uploads() == 1
    assert await service.cleanup_stale_uploads() == 0
    assert catalog.records[initiated.file.id].failure_reason == "upload_expired"


@pytest.mark.asyncio
async def test_deleted_object_cleanup_keeps_referenced_tombstone_without_redeleting() -> None:
    service, catalog, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="a.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    blobs.upload(initiated.file, b"a")
    await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)
    deleted = await service.delete_file(principal_id="principal-a", file_id=initiated.file.id)
    catalog.records[deleted.id] = replace(
        deleted,
        deleted_at=datetime.now(UTC) - timedelta(days=8),
    )
    catalog.referenced_file_ids.add(deleted.id)

    assert await service.cleanup_deleted_files() == 1
    assert catalog.records[deleted.id].object_deleted_at is not None
    assert blobs.deleted == [deleted.object_key]
    assert await service.cleanup_deleted_files() == 0
    assert blobs.deleted == [deleted.object_key]

    catalog.referenced_file_ids.remove(deleted.id)
    assert await service.cleanup_deleted_files() == 1
    assert deleted.id not in catalog.records
    assert blobs.deleted == [deleted.object_key]


@pytest.mark.asyncio
async def test_rate_limits_are_separate_by_principal_and_operation() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    limiter = PrincipalRateLimiter(clock=lambda: 100.0)
    service = FileService(
        catalog,
        blobs,
        max_bytes=1024,
        presign_ttl_seconds=600,
        init_rate_per_minute=1,
        complete_rate_per_minute=1,
        rate_limiter=limiter,
    )
    first = await service.initiate_upload(
        principal_id="principal-a",
        original_name="a.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    with pytest.raises(FileServiceError) as exc:
        await service.initiate_upload(
            principal_id="principal-a",
            original_name="b.txt",
            mime_type="text/plain",
            size_bytes=1,
        )
    assert exc.value.code == "rate_limited"
    assert exc.value.retry_after == 60

    await service.initiate_upload(
        principal_id="principal-b",
        original_name="b.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    blobs.upload(first.file, b"a")
    assert (
        await service.complete_upload(principal_id="principal-a", file_id=first.file.id)
    ).status == "uploaded"


@pytest.mark.asyncio
async def test_quota_allows_exact_limit_and_rejects_excess() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    service = FileService(
        catalog,
        blobs,
        max_bytes=10,
        presign_ttl_seconds=600,
        principal_quota_bytes=10,
    )
    for name, size in (("a.txt", 4), ("b.txt", 6)):
        await service.initiate_upload(
            principal_id="principal-a",
            original_name=name,
            mime_type="text/plain",
            size_bytes=size,
        )
    with pytest.raises(FileServiceError) as exc:
        await service.initiate_upload(
            principal_id="principal-a",
            original_name="c.txt",
            mime_type="text/plain",
            size_bytes=1,
        )
    assert exc.value.code == "storage_quota_exceeded"
    assert not blobs.objects


@pytest.mark.asyncio
async def test_pending_upload_cap_rejects_eleventh_record() -> None:
    service, catalog, _ = _service()
    for index in range(10):
        await service.initiate_upload(
            principal_id="principal-a",
            original_name=f"{index}.txt",
            mime_type="text/plain",
            size_bytes=1,
        )
    with pytest.raises(FileServiceError) as exc:
        await service.initiate_upload(
            principal_id="principal-a",
            original_name="11.txt",
            mime_type="text/plain",
            size_bytes=1,
        )
    assert exc.value.code == "too_many_pending_uploads"
    assert len(catalog.records) == 10


@pytest.mark.asyncio
async def test_file_audit_uses_allowlisted_details_without_storage_secrets() -> None:
    service, catalog, blobs = _service()
    actor = FileAuditActor(subject="partner-user", identity_provider_id=None)
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="private-name.txt",
        mime_type="text/plain",
        size_bytes=1,
        metadata={"assertion": "secret-assertion"},
        actor=actor,
        correlation_id="request-1",
    )
    blobs.upload(initiated.file, b"a")
    await service.complete_upload(
        principal_id="principal-a", file_id=initiated.file.id, actor=actor
    )
    await service.create_download_url(
        principal_id="principal-a", file_id=initiated.file.id, actor=actor
    )
    await service.delete_file(
        principal_id="principal-a", file_id=initiated.file.id, actor=actor
    )

    assert [event.action for event in catalog.audit_events] == [
        "file.upload_initiated",
        "file.upload_completed",
        "file.download_url_created",
        "file.deleted",
    ]
    serialized = repr([event.__dict__ for event in catalog.audit_events])
    for forbidden in (
        "private-name.txt",
        initiated.file.object_key,
        "storage.test",
        "secret-assertion",
        "Authorization",
    ):
        assert forbidden not in serialized
    assert all(event.actor_subject == "partner-user" for event in catalog.audit_events)


@pytest.mark.asyncio
async def test_audit_failure_does_not_return_download_url() -> None:
    service, catalog, blobs = _service()
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="a.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    blobs.upload(initiated.file, b"a")
    await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)
    catalog.fail_audit = True

    with pytest.raises(FileServiceError) as exc:
        await service.create_download_url(
            principal_id="principal-a", file_id=initiated.file.id
        )
    assert exc.value.status == 503
    assert exc.value.code == "audit_unavailable"


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_file_mutation() -> None:
    service, catalog, blobs = _service()
    catalog.fail_audit = True
    with pytest.raises(FileServiceError) as exc:
        await service.initiate_upload(
            principal_id="principal-a",
            original_name="a.txt",
            mime_type="text/plain",
            size_bytes=1,
        )
    assert exc.value.code == "audit_unavailable"
    assert catalog.records == {}

    catalog.fail_audit = False
    initiated = await service.initiate_upload(
        principal_id="principal-a",
        original_name="a.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    blobs.upload(initiated.file, b"a")
    await service.complete_upload(principal_id="principal-a", file_id=initiated.file.id)
    catalog.fail_audit = True
    with pytest.raises(FileServiceError) as exc:
        await service.delete_file(principal_id="principal-a", file_id=initiated.file.id)
    assert exc.value.code == "audit_unavailable"
    assert catalog.records[initiated.file.id].status == "uploaded"


@pytest.mark.asyncio
async def test_audit_retention_cleanup_is_bounded_and_idempotent() -> None:
    service, catalog, _ = _service()
    old = datetime.now(UTC) - timedelta(days=91)
    catalog.audit_events.extend(
        [
            FileAuditEvent(
                principal_id="principal-a",
                identity_provider_id=None,
                actor_subject="actor",
                file_id=None,
                action="file.attached",
                outcome="succeeded",
                occurred_at=old,
            ),
            FileAuditEvent(
                principal_id="principal-a",
                identity_provider_id=None,
                actor_subject="actor",
                file_id=None,
                action="file.attached",
                outcome="succeeded",
                occurred_at=datetime.now(UTC),
            ),
        ]
    )
    assert await service.cleanup_audit_events(limit=1) == 1
    assert await service.cleanup_audit_events(limit=1) == 0
    assert len(catalog.audit_events) == 1


@pytest.mark.asyncio
async def test_filename_rejects_del_without_rejecting_unicode() -> None:
    service, _, _ = _service()
    with pytest.raises(FileServiceError):
        await service.initiate_upload(
            principal_id="principal-a",
            original_name="bad\x7fname.txt",
            mime_type="text/plain",
            size_bytes=1,
        )
    valid = await service.initiate_upload(
        principal_id="principal-a",
        original_name="báo-cáo.txt",
        mime_type="text/plain",
        size_bytes=1,
    )
    assert valid.file.original_name == "báo-cáo.txt"
