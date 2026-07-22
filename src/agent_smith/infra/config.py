import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
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
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
    )
    mcp_credentials_key: str | None = Field(
        default=None,
        validation_alias="MCP_CREDENTIALS_KEY",
    )
    identity_secrets_key: str | None = Field(
        default=None,
        validation_alias="AGENT_SMITH_IDENTITY_SECRETS_KEY",
    )
    http_docs_enabled: bool = Field(
        default=True,
        validation_alias="AGENT_SMITH_HTTP_DOCS_ENABLED",
    )
    default_permission_mode: str = Field(
        default="default",
        validation_alias=AliasChoices(
            "AGENT_SMITH_DEFAULT_PERMISSION_MODE",
            "DEFAULT_PERMISSION_MODE",
        ),
    )
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
    s3_provider: Literal["minio", "r2", "aws"] = Field(
        default="minio",
        validation_alias="AGENT_SMITH_S3_PROVIDER",
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
def get_runtime_settings() -> RuntimeSettings:
    return RuntimeSettings()


def validate_runtime_startup(
    settings: RuntimeSettings,
    *,
    env: Mapping[str, str] | None = None,
    require_llm: bool = True,
) -> None:
    """Fail fast on configuration required by a runtime process."""
    source = os.environ if env is None else env
    errors = _storage_configuration_errors(settings)

    if require_llm:
        if not _configured(settings.openrouter_api_key):
            errors.append(
                "OPENROUTER_API_KEY is required because the model catalog routes through OpenRouter"
            )

        search_provider = source.get("AGENT_SMITH_WEB_SEARCH_PROVIDER", "").strip().lower()
        search_keys = {"tavily": "TAVILY_API_KEY", "brave": "BRAVE_SEARCH_API_KEY"}
        if search_provider and search_provider not in search_keys:
            errors.append(
                "AGENT_SMITH_WEB_SEARCH_PROVIDER must be either 'tavily' or 'brave' when set"
            )
        elif search_provider:
            required_key = search_keys[search_provider]
            if not _configured(source.get(required_key)):
                errors.append(
                    f"{required_key} is required when AGENT_SMITH_WEB_SEARCH_PROVIDER="
                    f"{search_provider}"
                )

    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise RuntimeError(f"Invalid Agent Smith startup configuration:\n{details}")


def _storage_configuration_errors(settings: RuntimeSettings) -> list[str]:
    provider = settings.s3_provider
    endpoint = (settings.s3_endpoint_url or "").strip()
    access_key = settings.s3_access_key_id.strip()
    secret_key = settings.s3_secret_access_key.strip()
    errors: list[str] = []

    if not endpoint and provider != "aws":
        errors.append(f"AGENT_SMITH_S3_ENDPOINT_URL is required for provider '{provider}'")
    if not access_key or not secret_key:
        errors.append(
            "AGENT_SMITH_S3_ACCESS_KEY_ID and AGENT_SMITH_S3_SECRET_ACCESS_KEY are required"
        )

    if provider == "r2":
        hostname = (urlsplit(endpoint).hostname or "").lower()
        if not hostname.endswith(".r2.cloudflarestorage.com"):
            errors.append(
                "Cloudflare R2 endpoint must end with '.r2.cloudflarestorage.com'"
            )
        if settings.s3_region != "auto":
            errors.append("Cloudflare R2 requires AGENT_SMITH_S3_REGION=auto")
        if settings.s3_path_style:
            errors.append("Cloudflare R2 requires AGENT_SMITH_S3_PATH_STYLE=false")
        if access_key == "smith" or secret_key == "smithsmith":
            errors.append("Cloudflare R2 credentials must replace the local MinIO credentials")
    elif provider == "aws":
        if endpoint:
            errors.append("AWS S3 uses its standard endpoint; leave AGENT_SMITH_S3_ENDPOINT_URL empty")
        if settings.s3_region == "auto":
            errors.append("AWS S3 requires an AWS region such as 'ap-southeast-1', not 'auto'")
        if settings.s3_path_style:
            errors.append("AWS S3 requires AGENT_SMITH_S3_PATH_STYLE=false")
    elif provider == "minio" and not endpoint.startswith(("http://", "https://")):
        errors.append("MinIO endpoint must be an http(s) URL")

    return errors


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())


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
