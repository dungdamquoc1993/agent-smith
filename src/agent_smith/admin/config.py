"""Configuration shared by the admin CLI and standalone Admin HTTP process."""

from urllib.parse import urlsplit
import ipaddress
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdminHttpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    postgres_url: str = Field(
        default="postgresql+asyncpg://smith:smith@localhost:5432/agent_smith",
        validation_alias="AGENT_SMITH_ADMIN_POSTGRES_URL",
    )
    host: str = Field(default="127.0.0.1", validation_alias="AGENT_SMITH_ADMIN_HOST")
    port: int = Field(default=8766, ge=1, le=65535, validation_alias="AGENT_SMITH_ADMIN_PORT")
    public_origin: str = Field(
        default="http://127.0.0.1:5174",
        validation_alias="AGENT_SMITH_ADMIN_PUBLIC_ORIGIN",
    )
    admin_ui_dist: Path | None = Field(
        default=None,
        validation_alias="AGENT_SMITH_ADMIN_UI_DIST",
    )
    cookie_secure: bool = Field(
        default=False,
        validation_alias="AGENT_SMITH_ADMIN_COOKIE_SECURE",
    )
    trusted_proxies: str = Field(
        default="",
        validation_alias="AGENT_SMITH_ADMIN_TRUSTED_PROXIES",
    )
    http_docs_enabled: bool = Field(
        default=True,
        validation_alias="AGENT_SMITH_ADMIN_HTTP_DOCS_ENABLED",
    )
    identity_secrets_key: str | None = Field(
        default=None,
        validation_alias="AGENT_SMITH_IDENTITY_SECRETS_KEY",
    )

    @field_validator("public_origin")
    @classmethod
    def validate_public_origin(cls, value: str) -> str:
        origin = value.strip()
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("public origin must be an exact http(s) origin without a path")
        return origin

    @model_validator(mode="after")
    def validate_secure_origin(self) -> "AdminHttpSettings":
        if self.cookie_secure and not self.public_origin.startswith("https://"):
            raise ValueError("secure admin cookies require an https public origin")
        return self

    @field_validator("trusted_proxies")
    @classmethod
    def validate_trusted_proxies(cls, value: str) -> str:
        values = [item.strip() for item in value.split(",") if item.strip()]
        for item in values:
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid trusted proxy network: {item}") from exc
        return ",".join(values)

    @field_validator("admin_ui_dist", mode="before")
    @classmethod
    def normalize_admin_ui_dist(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def session_cookie_name(self) -> str:
        prefix = "__Host-" if self.cookie_secure else ""
        return f"{prefix}agent_smith_admin_session"

    @property
    def csrf_cookie_name(self) -> str:
        prefix = "__Host-" if self.cookie_secure else ""
        return f"{prefix}agent_smith_admin_csrf"
