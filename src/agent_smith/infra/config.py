from functools import lru_cache

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
        default=900,
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
