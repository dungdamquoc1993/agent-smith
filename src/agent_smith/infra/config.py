from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://smith:smith@localhost:5432/agent_smith"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    mcp_credentials_key: str | None = None
    identity_secrets_key: str | None = Field(
        default=None,
        validation_alias="AGENT_SMITH_IDENTITY_SECRETS_KEY",
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
