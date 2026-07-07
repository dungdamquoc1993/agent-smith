"""Resolve and sanitize runtime metadata for agent context."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from agent_smith.app.invocation import AgentInvocation, VerifiedActor
from agent_smith.core.llm.types import JsonObject, JsonValue

MAX_CONTEXT_METADATA_BYTES = 16 * 1024
SECRET_KEY_PARTS = ("token", "secret", "password", "authorization", "cookie")
REDACTED = "[REDACTED]"


class ContextResolutionError(Exception):
    pass


class ContextResolver:
    def resolve(
        self,
        *,
        invocation: AgentInvocation,
        actor: VerifiedActor,
        principal_id: str,
        transport: str = "http_sse",
    ) -> tuple[JsonObject, JsonObject, JsonObject]:
        stable_context = _sanitize_json_object(
            {
                "actor": {
                    "principalId": principal_id,
                    "provider": actor.actor.provider,
                    "subject": actor.actor.subject,
                    "displayName": actor.actor.display_name,
                    "email": actor.actor.email,
                    "roles": actor.actor.roles,
                    "department": actor.actor.department,
                },
                "origin": {
                    "issuer": actor.issuer,
                    "transport": transport,
                    "externalSessionId": invocation.session.external_session_id,
                    "correlationId": invocation.correlation_id,
                    "traceId": invocation.trace_id,
                },
                "auth": {
                    "method": "trusted_app_assertion",
                    "upstreamAuth": _upstream_auth_for_context(actor),
                },
            }
        )
        turn_context = _sanitize_json_object(
            {
                "surface": invocation.surface.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                ),
                "metadata": {"app": invocation.metadata},
                "receivedAt": datetime.now(UTC).isoformat(),
            }
        )
        session_provenance = _sanitize_json_object(
            {
                "source": "app_assertion",
                "trigger": "user",
                "issuer": actor.issuer,
                "actorProvider": actor.actor.provider,
                "actorSubject": actor.actor.subject,
                "externalSessionId": invocation.session.external_session_id,
                "correlationId": invocation.correlation_id,
                "traceId": invocation.trace_id,
            }
        )
        _ensure_context_size(stable_context)
        _ensure_context_size(turn_context)
        return stable_context, turn_context, session_provenance


def _upstream_auth_for_context(actor: VerifiedActor) -> JsonObject | None:
    upstream = actor.actor.upstream_auth
    if not isinstance(upstream, dict):
        return None
    return {
        key: value
        for key, value in upstream.items()
        if key in {"provider", "assurance", "authTime", "method"}
    }


def _sanitize_json_object(value: dict[str, Any]) -> JsonObject:
    sanitized = _sanitize_value(value)
    if not isinstance(sanitized, dict):
        return {}
    return _drop_empty(sanitized)


def _sanitize_value(value: Any, *, key: str | None = None) -> JsonValue:
    if key is not None and any(part in key.lower() for part in SECRET_KEY_PARTS):
        return REDACTED
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _drop_empty(value: dict[str, JsonValue]) -> JsonObject:
    result: JsonObject = {}
    for key, item in value.items():
        if isinstance(item, dict):
            nested = _drop_empty(item)
            if nested:
                result[key] = nested
        elif isinstance(item, list):
            result[key] = [_drop_empty(v) if isinstance(v, dict) else v for v in item]
        elif item is not None:
            result[key] = item
    return result


def _ensure_context_size(metadata: JsonObject) -> None:
    payload = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    if len(payload) > MAX_CONTEXT_METADATA_BYTES:
        raise ContextResolutionError("Resolved context metadata exceeds the 16KB limit.")
