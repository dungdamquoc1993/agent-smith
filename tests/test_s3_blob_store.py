from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from agent_smith.app.ports.files import BlobStorageError
from agent_smith.infra.storage.s3 import S3BlobStore


@pytest.mark.asyncio
async def test_upload_presign_signs_content_type_and_optional_checksum() -> None:
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://storage.test/signed"
    store = S3BlobStore(client, bucket="private")
    sha256 = hashlib.sha256(b"hello").hexdigest()

    request = await store.create_upload_url(
        object_key="principals/p/files/f/original",
        mime_type="text/plain",
        size_bytes=5,
        sha256=sha256,
        expires_in_seconds=900,
    )

    params = client.generate_presigned_url.call_args.kwargs["Params"]
    assert params["Bucket"] == "private"
    assert params["ContentType"] == "text/plain"
    assert base64.b64decode(params["ChecksumSHA256"]).hex() == sha256
    assert request.headers["x-amz-checksum-sha256"] == params["ChecksumSHA256"]
    assert request.method == "PUT"


@pytest.mark.asyncio
async def test_stat_range_read_and_delete() -> None:
    client = MagicMock()
    checksum = base64.b64encode(hashlib.sha256(b"hello").digest()).decode()
    client.head_object.return_value = {
        "ContentLength": 5,
        "ContentType": "text/plain",
        "ETag": '"etag"',
        "ChecksumSHA256": checksum,
    }
    client.get_object.return_value = {"Body": BytesIO(b"hello")}
    store = S3BlobStore(client, bucket="private")

    stat = await store.stat(object_key="key")
    data = await store.read_range(object_key="key", start=0, end=4)
    await store.delete(object_key="key")

    assert stat is not None
    assert stat.checksum_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert stat.etag == "etag"
    assert data == b"hello"
    client.get_object.assert_called_once_with(Bucket="private", Key="key", Range="bytes=0-4")
    client.delete_object.assert_called_once_with(Bucket="private", Key="key")


@pytest.mark.asyncio
async def test_not_found_and_s3_errors_are_mapped() -> None:
    client = MagicMock()
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "HeadObject",
    )
    store = S3BlobStore(client, bucket="private")
    assert await store.stat(object_key="missing") is None

    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
        "HeadObject",
    )
    with pytest.raises(BlobStorageError, match="inspect"):
        await store.stat(object_key="private")


@pytest.mark.asyncio
async def test_stat_falls_back_for_provider_without_checksum_mode() -> None:
    client = MagicMock()
    client.head_object.side_effect = [
        ClientError(
            {
                "Error": {"Code": "NotImplemented"},
                "ResponseMetadata": {"HTTPStatusCode": 501},
            },
            "HeadObject",
        ),
        {"ContentLength": 5, "ContentType": "text/plain", "ETag": '"etag"'},
    ]
    store = S3BlobStore(client, bucket="private")

    stat = await store.stat(object_key="key")

    assert stat is not None and stat.size_bytes == 5
    assert client.head_object.call_args_list[1].kwargs == {"Bucket": "private", "Key": "key"}
