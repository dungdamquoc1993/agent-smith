"""Small infrastructure factories shared by process composition roots."""

import asyncio
from collections.abc import Awaitable

from agent_smith.infra.config import RuntimeSettings
from agent_smith.infra.storage.postgres import PostgresRuntime
from agent_smith.infra.storage.s3 import S3BlobStore, create_s3_client


def create_postgres_runtime(settings: RuntimeSettings) -> PostgresRuntime:
    return PostgresRuntime(settings.postgres_url)


def create_blob_store(settings: RuntimeSettings) -> S3BlobStore:
    return S3BlobStore(
        create_s3_client(
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            path_style=settings.s3_path_style,
        ),
        bucket=settings.s3_bucket,
    )


async def check_startup_dependencies(
    *,
    postgres: PostgresRuntime,
    blobs: S3BlobStore | None = None,
    storage_provider: str | None = None,
) -> None:
    """Verify process-critical services before accepting work."""
    checks: list[tuple[str, Awaitable[None]]] = [("PostgreSQL", postgres.check())]
    if blobs is not None:
        checks.append((f"S3 object storage ({storage_provider or 'configured'})", blobs.check()))

    results = await asyncio.gather(
        *(check for _, check in checks),
        return_exceptions=True,
    )
    cancelled = next(
        (result for result in results if isinstance(result, asyncio.CancelledError)),
        None,
    )
    if cancelled is not None:
        raise cancelled
    failures = [
        f"{name}: {result}"
        for (name, _), result in zip(checks, results, strict=True)
        if isinstance(result, Exception)
    ]
    if failures:
        details = "\n".join(f"- {failure}" for failure in failures)
        raise RuntimeError(f"Required startup dependencies are unavailable:\n{details}")
