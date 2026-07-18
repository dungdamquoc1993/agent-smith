import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_url: str = Field(
        default="postgresql+asyncpg://smith:smith@localhost:5432/agent_smith",
        validation_alias="AGENT_SMITH_POSTGRES_URL",
    )
    default_model: str = Field(
        default="gpt-5.5",
        validation_alias="AGENT_SMITH_DEFAULT_MODEL",
    )
    openrouter_api_key: str | None = None
    mcp_credentials_key: str | None = None
    admin_token: str | None = Field(
        default=None,
        validation_alias="AGENT_SMITH_ADMIN_TOKEN",
    )
    identity_secrets_key: str | None = Field(
        default=None,
        validation_alias="AGENT_SMITH_IDENTITY_SECRETS_KEY",
    )
    http_docs_enabled: bool = Field(
        default=True,
        validation_alias="AGENT_SMITH_HTTP_DOCS_ENABLED",
    )
    default_permission_mode: str = "default"
    assertion_audience: str = Field(
        default="agent-smith",
        validation_alias="AGENT_SMITH_ASSERTION_AUDIENCE",
    )
    trusted_apps_json: str = Field(
        default="{}",
        validation_alias="AGENT_SMITH_TRUSTED_APPS_JSON",
    )
    s3_endpoint_url: str | None = Field(
        default="http://localhost:9000",
        validation_alias="AGENT_SMITH_S3_ENDPOINT_URL",
    )
    s3_region: str = Field(
        default="us-east-1",
        min_length=1,
        validation_alias="AGENT_SMITH_S3_REGION",
    )
    s3_bucket: str = Field(
        default="agent-smith",
        min_length=1,
        validation_alias="AGENT_SMITH_S3_BUCKET",
    )
    s3_access_key_id: str = Field(
        default="smith",
        min_length=1,
        validation_alias="AGENT_SMITH_S3_ACCESS_KEY_ID",
    )
    s3_secret_access_key: str = Field(
        default="smithsmith",
        min_length=1,
        validation_alias="AGENT_SMITH_S3_SECRET_ACCESS_KEY",
    )
    s3_path_style: bool = Field(
        default=True,
        validation_alias="AGENT_SMITH_S3_PATH_STYLE",
    )
    s3_presign_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        validation_alias="AGENT_SMITH_S3_PRESIGN_TTL_SECONDS",
    )
    file_max_bytes: int = Field(
        default=50 * 1024 * 1024,
        gt=0,
        validation_alias="AGENT_SMITH_FILE_MAX_BYTES",
    )
    file_pending_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        validation_alias="AGENT_SMITH_FILE_PENDING_TTL_SECONDS",
    )
    file_deleted_retention_seconds: int = Field(
        default=7 * 24 * 3600,
        ge=0,
        validation_alias="AGENT_SMITH_FILE_DELETED_RETENTION_SECONDS",
    )
    file_principal_quota_bytes: int = Field(
        default=5 * 1024 * 1024 * 1024,
        gt=0,
        validation_alias="AGENT_SMITH_FILE_PRINCIPAL_QUOTA_BYTES",
    )
    file_max_pending_uploads: int = Field(
        default=10,
        ge=1,
        validation_alias="AGENT_SMITH_FILE_MAX_PENDING_UPLOADS",
    )
    file_init_rate_per_minute: int = Field(
        default=30,
        ge=1,
        validation_alias="AGENT_SMITH_FILE_INIT_RATE_PER_MINUTE",
    )
    file_complete_rate_per_minute: int = Field(
        default=60,
        ge=1,
        validation_alias="AGENT_SMITH_FILE_COMPLETE_RATE_PER_MINUTE",
    )
    file_audit_retention_seconds: int = Field(
        default=90 * 24 * 3600,
        ge=0,
        validation_alias="AGENT_SMITH_FILE_AUDIT_RETENTION_SECONDS",
    )
    file_maintenance_interval_seconds: int = Field(
        default=300,
        ge=10,
        validation_alias="AGENT_SMITH_FILE_MAINTENANCE_INTERVAL_SECONDS",
    )
    attachment_max_count: int = Field(
        default=8,
        ge=1,
        validation_alias="AGENT_SMITH_ATTACHMENT_MAX_COUNT",
    )
    attachment_max_materialized_bytes: int = Field(
        default=20 * 1024 * 1024,
        gt=0,
        validation_alias="AGENT_SMITH_ATTACHMENT_MAX_MATERIALIZED_BYTES",
    )
    attachment_read_concurrency: int = Field(
        default=4,
        ge=1,
        validation_alias="AGENT_SMITH_ATTACHMENT_READ_CONCURRENCY",
    )
    file_processing_pipeline_version: str = Field(
        default="document-v1",
        validation_alias="AGENT_SMITH_FILE_PROCESSING_PIPELINE_VERSION",
    )
    file_processing_max_attempts: int = Field(
        default=5,
        ge=1,
        validation_alias="AGENT_SMITH_FILE_PROCESSING_MAX_ATTEMPTS",
    )
    file_processing_poll_seconds: float = Field(
        default=1.0,
        gt=0,
        validation_alias="AGENT_SMITH_FILE_PROCESSING_POLL_SECONDS",
    )
    file_processing_lease_seconds: int = Field(
        default=60,
        ge=10,
        validation_alias="AGENT_SMITH_FILE_PROCESSING_LEASE_SECONDS",
    )
    file_processing_heartbeat_seconds: int = Field(
        default=20,
        ge=1,
        validation_alias="AGENT_SMITH_FILE_PROCESSING_HEARTBEAT_SECONDS",
    )
    file_processing_timeout_seconds: int = Field(
        default=600,
        ge=10,
        validation_alias="AGENT_SMITH_FILE_PROCESSING_TIMEOUT_SECONDS",
    )
    attachment_document_context_max_tokens: int = Field(
        default=32_000,
        ge=1_000,
        validation_alias="AGENT_SMITH_ATTACHMENT_DOCUMENT_CONTEXT_MAX_TOKENS",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_environment(path: Path) -> None:
    """Load simple dotenv assignments without overriding the process environment."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
