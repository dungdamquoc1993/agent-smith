"""Typed contracts for parent-app agent invocations."""

from __future__ import annotations

from typing import Any

import uuid

from pydantic import BaseModel, Field, field_validator

from agent_smith.core.llm.types import JsonObject, JsonValue


class AttachmentInput(BaseModel):
    file_id: str = Field(alias="fileId")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("file_id")
    @classmethod
    def validate_file_id(cls, value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("fileId must be a UUID") from exc


class AgentInvocationPayload(BaseModel):
    prompt: str
    attachments: list[AttachmentInput] = Field(default_factory=list)
    agent_name: str | None = Field(default=None, alias="agentName")
    model_key: str | None = Field(default=None, alias="modelKey")

    model_config = {"populate_by_name": True}


class AgentInvocationSession(BaseModel):
    smith_session_id: str | None = Field(default=None, alias="smithSessionId")
    external_session_id: str | None = Field(default=None, alias="externalSessionId")

    model_config = {"populate_by_name": True}


class AgentInvocationSurface(BaseModel):
    app: str | None = None
    route: str | None = None
    origin: str | None = None
    locale: str | None = None
    timezone: str | None = None
    user_agent: str | None = Field(default=None, alias="userAgent")

    model_config = {"populate_by_name": True}


class AgentInvocation(BaseModel):
    payload: AgentInvocationPayload
    session: AgentInvocationSession = Field(default_factory=AgentInvocationSession)
    surface: AgentInvocationSurface = Field(default_factory=AgentInvocationSurface)
    metadata: JsonObject = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, alias="correlationId")
    trace_id: str | None = Field(default=None, alias="traceId")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")

    model_config = {"populate_by_name": True}


class ActorProfile(BaseModel):
    display_name: str | None = Field(default=None, alias="displayName")
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    department: str | None = None
    upstream_auth: JsonObject | None = Field(default=None, alias="upstreamAuth")

    model_config = {"populate_by_name": True, "extra": "allow"}

    def public_claims(self) -> dict[str, JsonValue]:
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        data.pop("provider", None)
        data.pop("subject", None)
        redacted = _redact_claims(data)
        return redacted if isinstance(redacted, dict) else {}


class VerifiedActor(BaseModel):
    issuer: str
    subject: str
    jti: str
    provider_id: str | None = Field(default=None, alias="providerId")
    provider_slug: str | None = Field(default=None, alias="providerSlug")
    expires_at: int = Field(alias="expiresAt")
    actor: ActorProfile
    raw_claims: dict[str, Any] = Field(default_factory=dict, alias="rawClaims")

    model_config = {"populate_by_name": True}


class ResolvedInvocation(BaseModel):
    invocation: AgentInvocation
    actor: VerifiedActor
    principal_id: str = Field(alias="principalId")
    stable_context: JsonObject = Field(default_factory=dict, alias="stableContext")
    turn_context: JsonObject = Field(default_factory=dict, alias="turnContext")
    session_provenance: JsonObject = Field(default_factory=dict, alias="sessionProvenance")

    model_config = {"populate_by_name": True}


def _redact_claims(value: Any, *, key: str | None = None) -> JsonValue:
    if key is not None and any(part in key.lower() for part in _SECRET_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact_claims(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_claims(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


_SECRET_KEY_PARTS = ("token", "secret", "password", "authorization", "cookie")
