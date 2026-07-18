from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent_smith.app.ports.admin import AdminActorContext, AdminAuditEvent
from agent_smith.app.ports.identity import (
    IdentityProviderRecord,
    ProviderApiKeyRecord,
    ProviderAssertionKeyRecord,
)
from agent_smith.app.services.identity_providers import (
    IdentityProviderControlError,
    IdentityProviderControlService,
)
from agent_smith.app.services.provider_auth import IdentityProviderSecretCodec

NOW = datetime(2026, 7, 18, tzinfo=UTC)
ACTOR = AdminActorContext(
    kind="admin_operator",
    identifier="admin",
    operator_id=str(uuid.uuid4()),
    session_id=str(uuid.uuid4()),
    request_id=str(uuid.uuid4()),
)


class CapturingControlStore:
    def __init__(self) -> None:
        self.provider = IdentityProviderRecord(
            id=str(uuid.uuid4()),
            slug="acme",
            issuer="acme",
            display_name="Acme",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
        self.api_key = ProviderApiKeyRecord(
            id=str(uuid.uuid4()),
            provider_id=self.provider.id,
            name="runtime",
            key_hash="internal-hash",
            key_prefix="ask_prefix",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
        self.assertion_key = ProviderAssertionKeyRecord(
            id=str(uuid.uuid4()),
            provider_id=self.provider.id,
            kid="v1",
            alg="HS256",
            encrypted_secret="internal-ciphertext",
            encryption_scheme="fernet-v1",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
        self.audits: list[AdminAuditEvent] = []

    async def create_provider(self, **values: Any) -> IdentityProviderRecord:
        self.audits.append(values["audit"])
        return self.provider

    async def list_providers(self, **values: Any) -> list[IdentityProviderRecord]:
        del values
        older = IdentityProviderRecord(
            id=str(uuid.uuid4()),
            slug="older",
            issuer="older",
            display_name="Older",
            status="active",
            created_at=NOW - timedelta(seconds=1),
        )
        return [self.provider, older]

    async def get_provider(self, provider_id: str) -> IdentityProviderRecord | None:
        return self.provider if provider_id == self.provider.id else None

    async def update_provider(
        self, provider_id: str, changes: dict[str, Any], audit: AdminAuditEvent
    ) -> IdentityProviderRecord | None:
        del provider_id, changes
        self.audits.append(audit)
        return self.provider

    async def create_api_key(self, **values: Any) -> ProviderApiKeyRecord | None:
        self.audits.append(values["audit"])
        return self.api_key

    async def list_api_keys(self, provider_id: str, **values: Any):
        del provider_id, values
        return [self.api_key]

    async def revoke_api_key(
        self, key_id: str, revoked_at: datetime, audit: AdminAuditEvent
    ) -> ProviderApiKeyRecord | None:
        del key_id, revoked_at
        self.audits.append(audit)
        return self.api_key

    async def create_assertion_key(self, **values: Any) -> ProviderAssertionKeyRecord | None:
        self.audits.append(values["audit"])
        return self.assertion_key

    async def list_provider_assertion_keys(self, provider_id: str, **values: Any):
        del provider_id, values
        return [self.assertion_key]

    async def revoke_assertion_key(
        self, key_id: str, revoked_at: datetime, audit: AdminAuditEvent
    ) -> ProviderAssertionKeyRecord | None:
        del key_id, revoked_at
        self.audits.append(audit)
        return self.assertion_key


@pytest.mark.asyncio
async def test_control_mutations_propagate_actor_and_never_serialize_internals() -> None:
    store = CapturingControlStore()
    service = IdentityProviderControlService(
        store,
        secret_codec=IdentityProviderSecretCodec(
            "qRRHCAy57pLAsGwfGoWV4M0HXDpBwYJ1E4sAbT9plak="
        ),
    )

    await service.create_provider(
        {"slug": "acme", "issuer": "acme", "displayName": "Acme"}, actor=ACTOR
    )
    api_key = await service.create_api_key(
        store.provider.id, {"name": "runtime"}, actor=ACTOR
    )
    assertion_key = await service.create_assertion_key(
        store.provider.id, {"kid": "v1"}, actor=ACTOR
    )

    assert all(event.actor == ACTOR for event in store.audits)
    assert api_key["apiKey"]["rawKey"].startswith("ask_")
    assert "keyHash" not in api_key["apiKey"]
    assert assertion_key["assertionKey"]["rawSecret"]
    assert "encryptedSecret" not in assertion_key["assertionKey"]
    listed_api_keys = await service.list_api_keys(store.provider.id)
    listed_assertion_keys = await service.list_assertion_keys(store.provider.id)
    assert "rawKey" not in listed_api_keys["apiKeys"][0]
    assert "rawSecret" not in listed_assertion_keys["assertionKeys"][0]


@pytest.mark.asyncio
async def test_control_pagination_validation_and_encryption_unavailability() -> None:
    store = CapturingControlStore()
    service = IdentityProviderControlService(store)
    page = await service.list_providers(limit=1)
    assert len(page["identityProviders"]) == 1
    assert page["nextCursor"]

    with pytest.raises(IdentityProviderControlError) as invalid_page:
        await service.list_providers(limit=201)
    assert invalid_page.value.status == 422
    with pytest.raises(IdentityProviderControlError) as invalid_body:
        await service.update_provider(store.provider.id, {}, actor=ACTOR)
    assert invalid_body.value.status == 422
    with pytest.raises(IdentityProviderControlError) as unavailable:
        await service.create_assertion_key(
            store.provider.id, {"kid": "v1"}, actor=ACTOR
        )
    assert unavailable.value.status == 503
