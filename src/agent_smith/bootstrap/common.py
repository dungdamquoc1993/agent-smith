"""Small infrastructure factories shared by process composition roots."""

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
