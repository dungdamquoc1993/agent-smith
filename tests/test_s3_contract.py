from __future__ import annotations

import uuid
from os import getenv
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest

from agent_smith.infra.storage.s3 import S3BlobStore, create_s3_client
from agent_smith.app.services.file_maintenance import FileMaintenanceService
from agent_smith.app.services.files import FileService
from helpers.files import FakeFileCatalog, FakeFileMaintenanceStore


@pytest.mark.asyncio
async def test_presigned_upload_contract_when_s3_endpoint_is_configured() -> None:
    endpoint = getenv("AGENT_SMITH_TEST_S3_ENDPOINT_URL")
    if not endpoint:
        pytest.skip("AGENT_SMITH_TEST_S3_ENDPOINT_URL is not configured")
    bucket = getenv("AGENT_SMITH_TEST_S3_BUCKET", "agent-smith")
    client = create_s3_client(
        endpoint_url=endpoint,
        region=getenv("AGENT_SMITH_TEST_S3_REGION", "us-east-1"),
        access_key_id=getenv("AGENT_SMITH_TEST_S3_ACCESS_KEY_ID", "smith"),
        secret_access_key=getenv("AGENT_SMITH_TEST_S3_SECRET_ACCESS_KEY", "smithsmith"),
        path_style=_path_style(endpoint),
    )
    store = S3BlobStore(client, bucket=bucket)
    object_key = f"contract-tests/{uuid.uuid4()}/original"
    upload = await store.create_upload_url(
        object_key=object_key,
        mime_type="text/plain",
        size_bytes=5,
        sha256=None,
        expires_in_seconds=300,
    )
    try:
        async with httpx.AsyncClient() as http:
            response = await http.put(upload.url, headers=upload.headers, content=b"hello")
        response.raise_for_status()
        stat = await store.stat(object_key=object_key)
        assert stat is not None and stat.size_bytes == 5
        assert await store.read_range(object_key=object_key, start=0, end=4) == b"hello"
    finally:
        await store.delete(object_key=object_key)


@pytest.mark.asyncio
async def test_private_r2_presign_scope_and_signed_headers_when_configured() -> None:
    endpoint = getenv("AGENT_SMITH_TEST_S3_ENDPOINT_URL")
    if not endpoint:
        pytest.skip("AGENT_SMITH_TEST_S3_ENDPOINT_URL is not configured")
    bucket = getenv("AGENT_SMITH_TEST_S3_BUCKET", "agent-smith")
    client = create_s3_client(
        endpoint_url=endpoint,
        region=getenv("AGENT_SMITH_TEST_S3_REGION", "auto"),
        access_key_id=getenv("AGENT_SMITH_TEST_S3_ACCESS_KEY_ID", "smith"),
        secret_access_key=getenv("AGENT_SMITH_TEST_S3_SECRET_ACCESS_KEY", "smithsmith"),
        path_style=_path_style(endpoint),
    )
    store = S3BlobStore(client, bucket=bucket)
    object_key = f"contract-tests/{uuid.uuid4()}/original"
    upload = await store.create_upload_url(
        object_key=object_key,
        mime_type="text/plain",
        size_bytes=5,
        sha256=None,
        expires_in_seconds=600,
    )
    wrong_key_url = _replace_final_path_segment(upload.url, "other")
    try:
        async with httpx.AsyncClient() as http:
            assert (await http.get(upload.url)).status_code in {400, 401, 403, 405}
            assert (
                await http.put(
                    upload.url,
                    headers={"Content-Type": "application/pdf"},
                    content=b"hello",
                )
            ).status_code in {400, 401, 403}
            assert (
                await http.put(
                    wrong_key_url,
                    headers=upload.headers,
                    content=b"hello",
                )
            ).status_code in {400, 401, 403}
            correct = await http.put(upload.url, headers=upload.headers, content=b"hello")
            correct.raise_for_status()

            anonymous_url = urlunsplit((*urlsplit(upload.url)[:3], "", ""))
            assert (await http.get(anonymous_url)).status_code in {400, 401, 403}

            download = await store.create_download_url(
                object_key=object_key,
                download_name="contract.txt",
                mime_type="text/plain",
                expires_in_seconds=600,
            )
            fetched = await http.get(download.url)
            fetched.raise_for_status()
            assert fetched.content == b"hello"
            assert (
                await http.get(_replace_final_path_segment(download.url, "other"))
            ).status_code in {400, 401, 403, 404}
    finally:
        await store.delete(object_key=object_key)


@pytest.mark.asyncio
async def test_full_file_lifecycle_against_r2_when_configured() -> None:
    endpoint = getenv("AGENT_SMITH_TEST_S3_ENDPOINT_URL")
    if not endpoint:
        pytest.skip("AGENT_SMITH_TEST_S3_ENDPOINT_URL is not configured")
    store = S3BlobStore(
        create_s3_client(
            endpoint_url=endpoint,
            region=getenv("AGENT_SMITH_TEST_S3_REGION", "auto"),
            access_key_id=getenv("AGENT_SMITH_TEST_S3_ACCESS_KEY_ID", "smith"),
            secret_access_key=getenv("AGENT_SMITH_TEST_S3_SECRET_ACCESS_KEY", "smithsmith"),
            path_style=_path_style(endpoint),
        ),
        bucket=getenv("AGENT_SMITH_TEST_S3_BUCKET", "agent-smith"),
    )
    catalog = FakeFileCatalog()
    service = FileService(
        catalog,
        store,
        max_bytes=50 * 1024 * 1024,
        presign_ttl_seconds=600,
    )
    maintenance = FileMaintenanceService(
        FakeFileMaintenanceStore(catalog),
        store,
        pending_ttl_seconds=3600,
        deleted_retention_seconds=0,
        audit_retention_seconds=90 * 24 * 3600,
    )
    initiated = await service.initiate_upload(
        principal_id=str(uuid.uuid4()),
        original_name="pilot.txt",
        mime_type="text/plain",
        size_bytes=5,
    )
    try:
        async with httpx.AsyncClient() as http:
            uploaded = await http.put(
                initiated.upload.url,
                headers=initiated.upload.headers,
                content=b"hello",
            )
            uploaded.raise_for_status()
            completed = await service.complete_upload(
                principal_id=initiated.file.principal_id,
                file_id=initiated.file.id,
            )
            assert completed.status == "uploaded"
            download = await service.create_download_url(
                principal_id=initiated.file.principal_id,
                file_id=initiated.file.id,
            )
            fetched = await http.get(download.url)
            fetched.raise_for_status()
            assert fetched.content == b"hello"
        await service.delete_file(
            principal_id=initiated.file.principal_id,
            file_id=initiated.file.id,
        )
        assert await maintenance.cleanup_deleted_files() == 1
        assert await store.stat(object_key=initiated.file.object_key) is None
    finally:
        await store.delete(object_key=initiated.file.object_key)


def _path_style(endpoint: str) -> bool:
    configured = getenv("AGENT_SMITH_TEST_S3_PATH_STYLE")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return "r2.cloudflarestorage.com" not in endpoint


def _replace_final_path_segment(url: str, replacement: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.rsplit("/", 1)[0] + f"/{replacement}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
