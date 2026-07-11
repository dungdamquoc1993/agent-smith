"""Parent-app assertion verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_smith.app.invocation import ActorProfile, VerifiedActor

MAX_ASSERTION_AGE_SECONDS = 300


class AppAssertionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TrustedAppConfig(BaseModel):
    alg: str = "HS256"
    keys: dict[str, str]

    model_config = {"populate_by_name": True}


@dataclass(frozen=True)
class TrustedApps:
    audience: str
    apps: dict[str, TrustedAppConfig]


class AppAssertionVerifier:
    def __init__(self, trusted_apps: TrustedApps) -> None:
        self.trusted_apps = trusted_apps

    def verify_for_provider(
        self,
        authorization: str | None,
        *,
        provider_id: str,
        provider_slug: str,
        issuer: str,
        keys: dict[str, str],
        alg: str = "HS256",
    ) -> VerifiedActor:
        token = _bearer_token(authorization)
        if not token:
            raise AppAssertionError("missing_assertion", "Missing bearer app assertion.")
        header, payload, signing_input, signature = _decode_jws(token)
        token_issuer = _required_string(payload, "iss")
        if token_issuer != issuer:
            raise AppAssertionError("issuer_mismatch", "App assertion issuer does not match provider.")

        header_alg = _required_string(header, "alg")
        if header_alg != alg or header_alg != "HS256":
            raise AppAssertionError("unsupported_alg", f"Unsupported assertion alg: {header_alg}")
        key_id = header.get("kid")
        key = _resolve_keys(keys, key_id if isinstance(key_id, str) else None)
        expected = hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise AppAssertionError("invalid_signature", "Invalid app assertion signature.")

        audience = payload.get("aud")
        allowed_audience = self.trusted_apps.audience
        if audience != allowed_audience and not (
            isinstance(audience, list) and allowed_audience in audience
        ):
            raise AppAssertionError("invalid_audience", "Invalid app assertion audience.")

        expires_at, _, jti, subject = _validate_registered_claims(payload)
        actor = _actor_from_payload(payload)

        return VerifiedActor(
            issuer=issuer,
            subject=subject,
            jti=jti,
            providerId=provider_id,
            providerSlug=provider_slug,
            expiresAt=expires_at,
            actor=actor,
            rawClaims=payload,
        )


def parse_trusted_apps(*, audience: str, raw_json: str) -> TrustedApps:
    try:
        raw = json.loads(raw_json or "{}")
    except json.JSONDecodeError as exc:
        raise AppAssertionError("invalid_trusted_apps", "trusted_apps_json is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise AppAssertionError("invalid_trusted_apps", "trusted_apps_json must be an object.")
    apps: dict[str, TrustedAppConfig] = {}
    for issuer, data in raw.items():
        if not isinstance(issuer, str) or not isinstance(data, dict):
            raise AppAssertionError("invalid_trusted_apps", "trusted app entries must be objects.")
        apps[issuer] = TrustedAppConfig.model_validate(data)
    return TrustedApps(audience=audience, apps=apps)


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    token = authorization[len(prefix) :].strip()
    return token or None


def _decode_jws(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AppAssertionError("invalid_assertion", "App assertion must be a compact JWS.")
    header_raw, payload_raw, signature_raw = parts
    try:
        header = json.loads(_b64url_decode(header_raw))
        payload = json.loads(_b64url_decode(payload_raw))
        signature = _b64url_decode(signature_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AppAssertionError("invalid_assertion", "App assertion is malformed.") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise AppAssertionError("invalid_assertion", "App assertion header and payload must be objects.")
    signing_input = f"{header_raw}.{payload_raw}".encode("ascii")
    return header, payload, signing_input, signature


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _resolve_keys(keys: dict[str, str], key_id: str | None) -> str:
    if key_id is not None:
        key = keys.get(key_id)
        if key is None:
            raise AppAssertionError("unknown_key", "Unknown app assertion key id.")
        return key
    if len(keys) == 1:
        return next(iter(keys.values()))
    raise AppAssertionError("missing_key_id", "App assertion kid is required for this issuer.")


def _actor_from_payload(payload: dict[str, Any]) -> ActorProfile:
    actor_payload = payload.get("actor")
    if not isinstance(actor_payload, dict):
        raise AppAssertionError("invalid_actor", "App assertion actor must be an object.")
    if "provider" in actor_payload or "subject" in actor_payload:
        raise AppAssertionError(
            "invalid_actor_identity_fields",
            "Actor payload must not include provider or subject.",
        )
    try:
        return ActorProfile.model_validate(actor_payload)
    except ValidationError as exc:
        raise AppAssertionError("invalid_actor", "App assertion actor is invalid.") from exc


def _validate_registered_claims(payload: dict[str, Any]) -> tuple[int, int, str, str]:
    now = int(time.time())
    expires_at = _required_int(payload, "exp")
    issued_at = _required_int(payload, "iat")
    if expires_at <= now:
        raise AppAssertionError("expired_assertion", "App assertion has expired.")
    if issued_at > now + 60:
        raise AppAssertionError("invalid_iat", "App assertion issued_at is in the future.")
    if expires_at - issued_at > MAX_ASSERTION_AGE_SECONDS:
        raise AppAssertionError("assertion_too_long_lived", "App assertion TTL is too long.")
    return expires_at, issued_at, _required_string(payload, "jti"), _required_string(payload, "sub")


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AppAssertionError("invalid_assertion", f"App assertion {key} must be a string.")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise AppAssertionError("invalid_assertion", f"App assertion {key} must be an integer.")
    return value
