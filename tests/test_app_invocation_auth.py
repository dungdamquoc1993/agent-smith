from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from os import getenv

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.app.auth import AppAssertionError, AppAssertionVerifier, parse_trusted_apps
from agent_smith.app.context import ContextResolutionError, ContextResolver
from agent_smith.app.invocation import AgentInvocation
from agent_smith.app.services.identity import PrincipalIdentityService
from agent_smith.app.services.provider_auth import (
    IdentityProviderAuthService,
    IdentityProviderSecretCodec,
    hash_provider_api_key,
    provider_api_key_prefix,
)
from agent_smith.app.services.sessions import SessionService
from agent_smith.infra.db.base import Base
from agent_smith.infra.db.models.principal import (
    AppAssertionNonce,
    ExternalIdentity,
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
    Principal,
)

PROVIDER_ID = "00000000-0000-0000-0000-0000000000ad"


def test_app_assertion_verifier_accepts_valid_hs256_jws() -> None:
    verifier = _verifier()
    token = _sign_assertion()

    actor = verifier.verify_for_provider(
        f"Bearer {token}",
        provider_id=PROVIDER_ID,
        provider_slug="adw",
        issuer="adw",
        keys={"v1": "secret"},
    )

    assert actor.issuer == "adw"
    assert actor.provider_id == PROVIDER_ID
    assert actor.provider_slug == "adw"
    assert actor.subject == "adw-user-1"
    assert actor.actor.upstream_auth == {
        "provider": "hris",
        "subject": "vana",
        "assurance": "asserted_by_adw",
    }


@pytest.mark.parametrize(
    ("claim_patch", "code"),
    [
        ({"aud": "other"}, "invalid_audience"),
        ({"exp": int(time.time()) - 1}, "expired_assertion"),
        ({"actor": {"provider": "hris"}}, "invalid_actor_identity_fields"),
    ],
)
def test_app_assertion_verifier_rejects_invalid_claims(claim_patch: dict, code: str) -> None:
    verifier = _verifier()
    token = _sign_assertion(claim_patch=claim_patch)

    with pytest.raises(AppAssertionError) as exc:
        verifier.verify_for_provider(
            f"Bearer {token}",
            provider_id=PROVIDER_ID,
            provider_slug="adw",
            issuer="adw",
            keys={"v1": "secret"},
        )

    assert exc.value.code == code


def test_context_resolver_redacts_secret_metadata_and_limits_size() -> None:
    actor = _verified_actor()
    invocation = AgentInvocation.model_validate(
        {
            "payload": {"prompt": "hello"},
            "session": {"externalSessionId": "adw-conv"},
            "surface": {
                "app": "adw",
                "route": "/oneai",
                "userAgent": "browser",
                "timezone": "Asia/Ho_Chi_Minh",
            },
            "metadata": {
                "authorization": "Bearer secret",
                "nested": {"refreshToken": "raw"},
            },
            "correlationId": "corr-1",
        }
    )

    stable, turn, provenance = ContextResolver().resolve(
        invocation=invocation,
        actor=actor,
        principal_id="principal-1",
    )

    assert stable["actor"]["principalId"] == "principal-1"
    assert stable["actor"]["providerSlug"] == "adw"
    assert stable["actor"]["subject"] == "adw-user-1"
    assert stable["auth"]["upstreamAuth"] == {"provider": "hris", "assurance": "asserted_by_adw"}
    assert turn["metadata"]["app"]["authorization"] == "[REDACTED]"
    assert turn["metadata"]["app"]["nested"]["refreshToken"] == "[REDACTED]"
    assert provenance["externalSessionId"] == "adw-conv"

    too_large = invocation.model_copy(update={"metadata": {"blob": "x" * 20_000}})
    with pytest.raises(ContextResolutionError):
        ContextResolver().resolve(invocation=too_large, actor=actor, principal_id="principal-1")


@pytest.mark.asyncio
async def test_identity_resolution_creates_app_scoped_identity_only_when_database_is_configured() -> None:
    database_url = getenv("AGENT_SMITH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENT_SMITH_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    subject = f"adw-user-{uuid.uuid4().hex}"
    provider_id = uuid.uuid4()
    actor = _verified_actor(subject=subject, provider_id=str(provider_id))
    service = PrincipalIdentityService(factory)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add(
                IdentityProvider(
                    id=provider_id,
                    slug="adw",
                    issuer="adw",
                    display_name="ADW",
                )
            )

        first = await service.resolve_principal(actor)
        second_actor = _verified_actor(
            subject=subject,
            provider_id=str(provider_id),
            jti=str(uuid.uuid4()),
        )
        second = await service.resolve_principal(second_actor)

        assert first.id == second.id
        async with factory() as db:
            identities = (
                await db.scalars(select(ExternalIdentity).where(ExternalIdentity.principal_id == first.id))
            ).all()
            assert [(identity.identity_provider_id, identity.subject) for identity in identities] == [
                (provider_id, subject)
            ]

        with pytest.raises(AppAssertionError) as exc:
            await service.resolve_principal(actor)
        assert exc.value.code == "replayed_assertion"
    finally:
        async with factory() as db, db.begin():
            await db.execute(delete(AppAssertionNonce).where(AppAssertionNonce.issuer == "adw"))
            await db.execute(delete(ExternalIdentity).where(ExternalIdentity.subject == subject))
            await db.execute(delete(Principal).where(Principal.display_name == "Nguyen Van A"))
            await db.execute(delete(IdentityProvider).where(IdentityProvider.id == provider_id))
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_auth_requires_api_key_and_prevents_provider_spoofing_when_database_is_configured() -> None:
    database_url = getenv("AGENT_SMITH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENT_SMITH_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider_id = uuid.uuid4()
    raw_api_key = f"ask_{uuid.uuid4().hex}"
    codec = IdentityProviderSecretCodec(_fernet_key())
    service = IdentityProviderAuthService(
        factory,
        assertion_verifier=_verifier(),
        secret_codec=codec,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add(
                IdentityProvider(
                    id=provider_id,
                    slug="adw",
                    issuer="adw",
                    display_name="ADW",
                )
            )
            db.add(
                IdentityProviderApiKey(
                    id=uuid.uuid4(),
                    provider_id=provider_id,
                    name="test",
                    key_hash=hash_provider_api_key(raw_api_key),
                    key_prefix=provider_api_key_prefix(raw_api_key),
                )
            )
            db.add(
                IdentityProviderAssertionKey(
                    id=uuid.uuid4(),
                    provider_id=provider_id,
                    kid="v1",
                    encrypted_secret=codec.encrypt("secret"),
                )
            )

        actor = await service.verify_invocation(
            provider_api_key=raw_api_key,
            authorization=f"Bearer {_sign_assertion()}",
        )
        assert actor.provider_id == str(provider_id)
        assert actor.provider_slug == "adw"
        assert actor.subject == "adw-user-1"

        with pytest.raises(AppAssertionError) as exc:
            await service.verify_invocation(
                provider_api_key=raw_api_key,
                authorization=f"Bearer {_sign_assertion(claim_patch={'actor': {'provider': 'evil'}})}",
            )
        assert exc.value.code == "invalid_actor_identity_fields"

        with pytest.raises(AppAssertionError) as exc:
            await service.verify_invocation(
                provider_api_key="wrong",
                authorization=f"Bearer {_sign_assertion()}",
            )
        assert exc.value.code == "invalid_provider_api_key"
    finally:
        async with factory() as db, db.begin():
            await db.execute(delete(AppAssertionNonce).where(AppAssertionNonce.issuer == "adw"))
            await db.execute(delete(IdentityProvider).where(IdentityProvider.id == provider_id))
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_service_rejects_cross_principal_session_when_database_is_configured() -> None:
    database_url = getenv("AGENT_SMITH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENT_SMITH_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = SessionService(factory, principal_display_name="unused")
    principal_a = uuid.uuid4()
    principal_b = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add_all(
                [
                    Principal(id=principal_a, display_name="Principal A"),
                    Principal(id=principal_b, display_name="Principal B"),
                ]
            )

        session = await service.open_or_create_session_for_principal(
            principal_id=str(principal_a),
            session_id=None,
            provenance={"issuer": "adw"},
        )
        metadata = await session.get_metadata()

        with pytest.raises(LookupError):
            await service.open_or_create_session_for_principal(
                principal_id=str(principal_b),
                session_id=metadata.id,
            )
    finally:
        async with factory() as db, db.begin():
            await db.execute(delete(Principal).where(Principal.id.in_([principal_a, principal_b])))
        await engine.dispose()


def _verifier() -> AppAssertionVerifier:
    return AppAssertionVerifier(
        parse_trusted_apps(
            audience="agent-smith",
            raw_json=json.dumps(
                {
                    "adw": {
                        "alg": "HS256",
                        "keys": {"v1": "secret"},
                    }
                }
            ),
        )
    )


def _verified_actor(
    *,
    subject: str = "adw-user-1",
    provider_id: str = PROVIDER_ID,
    jti: str | None = None,
):
    return _verifier().verify_for_provider(
        f"Bearer {_sign_assertion(subject=subject, jti=jti)}",
        provider_id=provider_id,
        provider_slug="adw",
        issuer="adw",
        keys={"v1": "secret"},
    )


def _sign_assertion(
    *,
    subject: str = "adw-user-1",
    jti: str | None = None,
    claim_patch: dict | None = None,
) -> str:
    now = int(time.time())
    claims = {
        "iss": "adw",
        "aud": "agent-smith",
        "sub": subject,
        "jti": jti or str(uuid.uuid4()),
        "iat": now,
        "exp": now + 300,
        "actor": {
            "displayName": "Nguyen Van A",
            "email": "a@company.vn",
            "roles": ["manager"],
            "department": "IT",
            "upstreamAuth": {
                "provider": "hris",
                "subject": "vana",
                "assurance": "asserted_by_adw",
            },
        },
    }
    if claim_patch:
        claims.update(claim_patch)
    header = {"alg": "HS256", "typ": "JWT", "kid": "v1"}
    signing_input = f"{_b64(header)}.{_b64(claims)}"
    signature = hmac.new(b"secret", signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64_bytes(signature)}"


def _b64(value: dict) -> str:
    return _b64_bytes(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _b64_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _fernet_key() -> str:
    return "qRRHCAy57pLAsGwfGoWV4M0HXDpBwYJ1E4sAbT9plak="
