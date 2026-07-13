from __future__ import annotations

import json
import uuid
from os import getenv
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.app.auth import AppAssertionError, AppAssertionVerifier, parse_trusted_apps
from agent_smith.app.services.identity_providers import (
    IdentityProviderManagementError,
    IdentityProviderManagementService,
)
from agent_smith.app.services.provider_auth import (
    IdentityProviderAuthService,
    IdentityProviderSecretCodec,
)
from agent_smith.infra.db.base import Base
from agent_smith.infra.db.models.principal import (
    IdentityProvider,
    IdentityProviderApiKey,
    IdentityProviderAssertionKey,
)
from agent_smith.transports.http.main import create_app


@pytest.mark.asyncio
async def test_identity_provider_management_lifecycle_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")

    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    codec = IdentityProviderSecretCodec(_fernet_key())
    management = IdentityProviderManagementService(factory, secret_codec=codec)
    auth = IdentityProviderAuthService(
        factory,
        assertion_verifier=AppAssertionVerifier(
            parse_trusted_apps(audience="agent-smith", raw_json="{}")
        ),
        secret_codec=codec,
    )
    slug = f"provider_{uuid.uuid4().hex[:12]}"
    issuer = f"issuer-{uuid.uuid4().hex}"
    provider_id = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        created = await management.create_provider(
            {
                "slug": slug,
                "issuer": issuer,
                "displayName": "Acme HR",
                "metadata": {"tier": "dev"},
            }
        )
        provider = created["identityProvider"]
        provider_id = provider["id"]
        assert provider["slug"] == slug

        with pytest.raises(IdentityProviderManagementError) as duplicate:
            await management.create_provider(
                {"slug": slug, "issuer": f"{issuer}-2", "displayName": "Duplicate"}
            )
        assert duplicate.value.code == "identity_provider_conflict"

        updated = await management.update_provider(provider_id, {"displayName": "Acme"})
        assert updated["identityProvider"]["displayName"] == "Acme"

        api_key = (
            await management.create_api_key(provider_id, {"name": "runtime v1"})
        )["apiKey"]
        raw_api_key = api_key["rawKey"]
        assert raw_api_key.startswith("ask_")
        assert "keyHash" not in api_key
        assert "encryptedSecret" not in api_key

        assertion_key = (
            await management.create_assertion_key(provider_id, {"kid": "v1"})
        )["assertionKey"]
        raw_secret = assertion_key["rawSecret"]
        assert raw_secret
        assert "encryptedSecret" not in assertion_key

        async with factory() as db:
            stored_api_key = (
                await db.scalars(
                    select(IdentityProviderApiKey).where(
                        IdentityProviderApiKey.id == uuid.UUID(api_key["id"])
                    )
                )
            ).one()
            stored_assertion_key = (
                await db.scalars(
                    select(IdentityProviderAssertionKey).where(
                        IdentityProviderAssertionKey.id == uuid.UUID(assertion_key["id"])
                    )
                )
            ).one()
            assert stored_api_key.key_hash != raw_api_key
            assert stored_api_key.key_prefix == raw_api_key[:16]
            assert stored_assertion_key.encrypted_secret != raw_secret
            assert codec.decrypt(stored_assertion_key.encrypted_secret) == raw_secret

        actor = await auth.verify_invocation(
            provider_api_key=raw_api_key,
            authorization=f"Bearer {_sign_assertion(issuer=issuer, secret=raw_secret)}",
        )
        assert actor.provider_id == provider_id
        assert actor.provider_slug == slug
        assert actor.subject == "external-user-1"

        revoked_api_key = (await management.revoke_api_key(api_key["id"]))["apiKey"]
        assert revoked_api_key["status"] == "revoked"
        assert revoked_api_key["revokedAt"]

        with pytest.raises(AppAssertionError) as revoked:
            await auth.verify_invocation(
                provider_api_key=raw_api_key,
                authorization=f"Bearer {_sign_assertion(issuer=issuer, secret=raw_secret)}",
            )
        assert revoked.value.code == "provider_api_key_revoked"

        revoked_assertion_key = (
            await management.revoke_assertion_key(assertion_key["id"])
        )["assertionKey"]
        assert revoked_assertion_key["status"] == "revoked"
        assert revoked_assertion_key["revokedAt"]
    finally:
        if provider_id:
            async with factory() as db, db.begin():
                await db.execute(delete(IdentityProvider).where(IdentityProvider.id == uuid.UUID(provider_id)))
        await engine.dispose()


def test_admin_identity_provider_routes_require_configured_admin_token() -> None:
    container = _fake_container(admin_token=None)
    with TestClient(create_app(container=container)) as client:
        status, payload = _request(client, "GET", "/api/admin/identity-providers")

    assert status == 500
    assert payload["error"]["code"] == "admin_auth_not_configured"


def test_admin_identity_provider_routes_reject_missing_or_wrong_admin_token() -> None:
    container = _fake_container(admin_token="secret-admin")
    with TestClient(create_app(container=container)) as client:
        status, payload = _request(client, "GET", "/api/admin/identity-providers")
        wrong_status, wrong_payload = _request(
            client,
            "GET",
            "/api/admin/identity-providers",
            token="wrong",
        )

    assert status == 401
    assert payload["error"]["code"] == "admin_unauthorized"
    assert wrong_status == 401
    assert wrong_payload["error"]["code"] == "admin_unauthorized"


def test_admin_identity_provider_routes_dispatch_provider_management() -> None:
    service = _FakeIdentityProviderService()
    container = _fake_container(admin_token="secret-admin", service=service)
    with TestClient(create_app(container=container)) as client:
        create_status, created = _request(
            client,
            "POST",
            "/api/admin/identity-providers",
            token="secret-admin",
            body={"slug": "acme", "issuer": "acme", "displayName": "Acme"},
        )
        list_status, listed = _request(
            client,
            "GET",
            "/api/admin/identity-providers",
            token="secret-admin",
        )
        update_status, updated = _request(
            client,
            "PATCH",
            "/api/admin/identity-providers/provider-1",
            token="secret-admin",
            body={"displayName": "Acme Updated"},
        )
        api_key_status, api_key = _request(
            client,
            "POST",
            "/api/admin/identity-providers/provider-1/api-keys",
            token="secret-admin",
            body={"name": "runtime"},
        )
        assertion_key_status, assertion_key = _request(
            client,
            "POST",
            "/api/admin/identity-providers/provider-1/assertion-keys",
            token="secret-admin",
            body={"kid": "v1"},
        )

    assert create_status == 201
    assert created["identityProvider"]["slug"] == "acme"
    assert list_status == 200
    assert listed["identityProviders"]
    assert update_status == 200
    assert updated["identityProvider"]["displayName"] == "Acme Updated"
    assert api_key_status == 201
    assert api_key["apiKey"]["rawKey"] == "ask_raw"
    assert "keyHash" not in api_key["apiKey"]
    assert assertion_key_status == 201
    assert assertion_key["assertionKey"]["rawSecret"] == "raw-secret"
    assert "encryptedSecret" not in assertion_key["assertionKey"]
    assert service.created_provider == {"slug": "acme", "issuer": "acme", "displayName": "Acme"}


def test_fastapi_root_route_listing() -> None:
    container = _fake_container(admin_token="secret-admin")
    with TestClient(create_app(container=container)) as client:
        response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "agent_smith_http"
    assert "/api/agent/invoke/stream" in payload["routes"]
    assert "/api/admin/identity-providers" in payload["routes"]


def test_agent_invoke_stream_reads_provider_headers() -> None:
    agent_runs = _FakeAgentRunService()
    container = _fake_container(admin_token="secret-admin", agent_runs=agent_runs)
    with TestClient(create_app(container=container)) as client:
        response = client.post(
            "/api/agent/invoke/stream",
            headers={
                "X-Agent-Smith-Provider-Key": "ask_raw",
                "Authorization": "Bearer signed-assertion",
            },
            json={"payload": {"prompt": "hi"}},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.completed" in response.text
    assert agent_runs.prepared_body == {"payload": {"prompt": "hi"}}
    assert agent_runs.provider_api_key == "ask_raw"
    assert agent_runs.authorization == "Bearer signed-assertion"


class _FakeIdentityProviderService:
    def __init__(self) -> None:
        self.created_provider: dict[str, Any] | None = None

    async def list_providers(self) -> dict[str, Any]:
        return {"identityProviders": [{"id": "provider-1", "slug": "acme"}]}

    async def get_provider(self, provider_id: str) -> dict[str, Any]:
        return {"identityProvider": {"id": provider_id, "slug": "acme"}}

    async def create_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.created_provider = payload
        return {"identityProvider": {"id": "provider-1", **payload}}

    async def update_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"identityProvider": {"id": provider_id, **payload}}

    async def create_api_key(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "apiKey": {
                "id": "api-key-1",
                "providerId": provider_id,
                "name": payload["name"],
                "keyPrefix": "ask_raw",
                "rawKey": "ask_raw",
            }
        }

    async def list_api_keys(self, provider_id: str) -> dict[str, Any]:
        return {"apiKeys": [{"id": "api-key-1", "providerId": provider_id}]}

    async def revoke_api_key(self, key_id: str) -> dict[str, Any]:
        return {"apiKey": {"id": key_id, "status": "revoked"}}

    async def create_assertion_key(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "assertionKey": {
                "id": "assertion-key-1",
                "providerId": provider_id,
                "kid": payload["kid"],
                "alg": "HS256",
                "rawSecret": "raw-secret",
            }
        }

    async def list_assertion_keys(self, provider_id: str) -> dict[str, Any]:
        return {"assertionKeys": [{"id": "assertion-key-1", "providerId": provider_id}]}

    async def revoke_assertion_key(self, key_id: str) -> dict[str, Any]:
        return {"assertionKey": {"id": key_id, "status": "revoked"}}


class _FakeAgentRunService:
    def __init__(self) -> None:
        self.provider_api_key: str | None = None
        self.authorization: str | None = None
        self.prepared_body: dict[str, Any] | None = None

    async def prepare_invocation(
        self,
        *,
        provider_api_key: str | None,
        authorization: str | None,
        body: dict[str, Any],
    ) -> dict[str, str]:
        self.provider_api_key = provider_api_key
        self.authorization = authorization
        self.prepared_body = body
        return {"prepared": "ok"}

    async def run_prepared_invocation_stream(self, prepared: Any, emit: Any) -> None:
        await emit("run.completed", {"prepared": prepared})


def _fake_container(
    *,
    admin_token: str | None,
    service: _FakeIdentityProviderService | None = None,
    agent_runs: _FakeAgentRunService | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(admin_token=admin_token),
        identity_providers=service or _FakeIdentityProviderService(),
        agent_runs=agent_runs or _FakeAgentRunService(),
    )


def _request(
    client: TestClient,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"content-type": "application/json"}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    response = client.request(method, path, content=json.dumps(body) if body is not None else None, headers=headers)
    return response.status_code, response.json()


def _fernet_key() -> str:
    return "qRRHCAy57pLAsGwfGoWV4M0HXDpBwYJ1E4sAbT9plak="


def _sign_assertion(*, issuer: str, secret: str) -> str:
    import base64
    import hashlib
    import hmac
    import time

    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": "agent-smith",
        "sub": "external-user-1",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 300,
        "actor": {
            "displayName": "External User",
            "email": "external@example.com",
        },
    }
    header = {"alg": "HS256", "typ": "JWT", "kid": "v1"}
    signing_input = f"{_b64(header)}.{_b64(claims)}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"


def _b64(value: dict[str, Any]) -> str:
    import base64

    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
