"""BlobStore backed by an S3-compatible API."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError

from agent_smith.app.ports.files import BlobObjectStat, BlobStorageError, PresignedRequest


class S3BlobStore:
    def __init__(self, client: Any, *, bucket: str) -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket is required")
        self._client = client
        self._bucket = bucket

    async def create_upload_url(
        self,
        *,
        object_key: str,
        mime_type: str,
        size_bytes: int,
        sha256: str | None,
        expires_in_seconds: int,
    ) -> PresignedRequest:
        del size_bytes  # S3 PUT presigning cannot enforce Content-Length portably.
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": object_key,
            "ContentType": mime_type,
        }
        headers = {"Content-Type": mime_type}
        if sha256 is not None:
            checksum = base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
            params["ChecksumSHA256"] = checksum
            headers["x-amz-checksum-sha256"] = checksum
        try:
            url = await asyncio.to_thread(
                self._client.generate_presigned_url,
                "put_object",
                Params=params,
                ExpiresIn=expires_in_seconds,
                HttpMethod="PUT",
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            raise BlobStorageError("Unable to create upload URL") from exc
        return PresignedRequest(
            url=url,
            method="PUT",
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
            headers=headers,
        )

    async def create_download_url(
        self,
        *,
        object_key: str,
        download_name: str,
        mime_type: str,
        expires_in_seconds: int,
    ) -> PresignedRequest:
        disposition = f"attachment; filename*=UTF-8''{quote(download_name, safe='')}"
        try:
            url = await asyncio.to_thread(
                self._client.generate_presigned_url,
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": object_key,
                    "ResponseContentType": mime_type,
                    "ResponseContentDisposition": disposition,
                },
                ExpiresIn=expires_in_seconds,
                HttpMethod="GET",
            )
        except (BotoCoreError, ClientError, ValueError) as exc:
            raise BlobStorageError("Unable to create download URL") from exc
        return PresignedRequest(
            url=url,
            method="GET",
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        )

    async def stat(self, *, object_key: str) -> BlobObjectStat | None:
        try:
            response = await asyncio.to_thread(
                self._client.head_object,
                Bucket=self._bucket,
                Key=object_key,
                ChecksumMode="ENABLED",
            )
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = exc.response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return None
            if code in {"InvalidArgument", "InvalidRequest", "NotImplemented", "XNotImplemented"}:
                response = await self._stat_without_checksum(object_key)
            else:
                raise BlobStorageError("Unable to inspect stored object") from exc
        except BotoCoreError as exc:
            raise BlobStorageError("Unable to inspect stored object") from exc
        checksum = response.get("ChecksumSHA256")
        checksum_hex = None
        if checksum:
            try:
                checksum_hex = base64.b64decode(checksum).hex()
            except (ValueError, TypeError):
                checksum_hex = None
        return BlobObjectStat(
            size_bytes=int(response["ContentLength"]),
            etag=str(response.get("ETag") or "").strip('"') or None,
            content_type=response.get("ContentType"),
            checksum_sha256=checksum_hex,
        )

    async def _stat_without_checksum(self, object_key: str) -> dict[str, Any]:
        """Fallback for S3-compatible providers that do not support ChecksumMode."""
        try:
            return await asyncio.to_thread(
                self._client.head_object,
                Bucket=self._bucket,
                Key=object_key,
            )
        except (BotoCoreError, ClientError) as exc:
            raise BlobStorageError("Unable to inspect stored object") from exc

    async def read_range(self, *, object_key: str, start: int, end: int) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._bucket,
                Key=object_key,
                Range=f"bytes={start}-{end}",
            )
            return await asyncio.to_thread(response["Body"].read)
        except (BotoCoreError, ClientError, KeyError) as exc:
            raise BlobStorageError("Unable to read stored object") from exc

    async def delete(self, *, object_key: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._bucket,
                Key=object_key,
            )
        except (BotoCoreError, ClientError) as exc:
            raise BlobStorageError("Unable to delete stored object") from exc
