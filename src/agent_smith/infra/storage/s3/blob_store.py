"""BlobStore backed by an S3-compatible API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
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

    async def check(self) -> None:
        """Check that the configured private bucket exists and is accessible."""
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
        except (BotoCoreError, ClientError, ValueError) as exc:
            raise BlobStorageError("configured bucket is not accessible") from exc

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

    async def read_object(self, *, object_key: str, max_bytes: int) -> bytes:
        if max_bytes < 0:
            raise ValueError("max_bytes must not be negative")
        body: Any | None = None
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._bucket,
                Key=object_key,
            )
            body = response["Body"]
            data = await asyncio.to_thread(body.read, max_bytes + 1)
            if len(data) > max_bytes:
                raise BlobStorageError("Stored object exceeds bounded read limit")
            return data
        except BlobStorageError:
            raise
        except (BotoCoreError, ClientError, KeyError) as exc:
            raise BlobStorageError("Unable to read stored object") from exc
        finally:
            if body is not None and callable(getattr(body, "close", None)):
                await asyncio.to_thread(body.close)

    async def write_object(
        self, *, object_key: str, data: bytes, mime_type: str
    ) -> BlobObjectStat:
        checksum = hashlib.sha256(data).digest()
        try:
            try:
                response = await asyncio.to_thread(
                    self._client.put_object,
                    Bucket=self._bucket,
                    Key=object_key,
                    Body=data,
                    ContentType=mime_type,
                    ChecksumSHA256=base64.b64encode(checksum).decode("ascii"),
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code not in {
                    "InvalidArgument",
                    "InvalidRequest",
                    "NotImplemented",
                    "XNotImplemented",
                }:
                    raise
                response = await asyncio.to_thread(
                    self._client.put_object,
                    Bucket=self._bucket,
                    Key=object_key,
                    Body=data,
                    ContentType=mime_type,
                )
        except (BotoCoreError, ClientError) as exc:
            raise BlobStorageError("Unable to write stored object") from exc
        return BlobObjectStat(
            size_bytes=len(data),
            etag=str(response.get("ETag") or "").strip('"') or None,
            content_type=mime_type,
            checksum_sha256=checksum.hex(),
        )

    async def delete(self, *, object_key: str) -> None:
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._bucket,
                Key=object_key,
            )
        except (BotoCoreError, ClientError) as exc:
            raise BlobStorageError("Unable to delete stored object") from exc

    async def delete_prefix(self, *, prefix: str) -> None:
        continuation: str | None = None
        try:
            while True:
                params: dict[str, Any] = {
                    "Bucket": self._bucket,
                    "Prefix": prefix,
                    "MaxKeys": 1000,
                }
                if continuation:
                    params["ContinuationToken"] = continuation
                response = await asyncio.to_thread(self._client.list_objects_v2, **params)
                objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
                if objects:
                    await asyncio.to_thread(
                        self._client.delete_objects,
                        Bucket=self._bucket,
                        Delete={"Objects": objects, "Quiet": True},
                    )
                if not response.get("IsTruncated"):
                    return
                continuation = response.get("NextContinuationToken")
        except (BotoCoreError, ClientError, KeyError) as exc:
            raise BlobStorageError("Unable to delete stored object prefix") from exc
