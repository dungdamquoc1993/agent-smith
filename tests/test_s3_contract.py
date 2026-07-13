from __future__ import annotations

import uuid
from os import getenv

import httpx
import pytest

from agent_smith.infra.storage.s3 import S3BlobStore, create_s3_client


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
        path_style=True,
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
