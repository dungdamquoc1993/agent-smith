from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agent_smith.app.services.files import FileService, FileServiceError
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
