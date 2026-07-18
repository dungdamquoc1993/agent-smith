from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent_smith.app.ports.admin import (
    AdminActorContext,
    AdminAuditEvent,
    AdminOperatorRecord,
    AdminSessionRecord,
)
from agent_smith.app.services.admin import (
    AdminAuthenticationError,
    AdminAuthenticationService,
    AdminOperatorService,
    AdminValidationError,
    SESSION_ABSOLUTE_TTL,
    SESSION_IDLE_TTL,
    hash_admin_token,
    normalize_admin_username,
    sanitize_audit_metadata,
)
from agent_smith.infra.admin.security import Argon2AdminPasswordHasher


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class FakeHasher:
    def __init__(self) -> None:
        self.verifications: list[tuple[str, str]] = []
        self.rehash = False

    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        self.verifications.append((password_hash, password))
        return password_hash == f"hashed:{password}"

    def needs_rehash(self, password_hash: str) -> bool:
        del password_hash
        return self.rehash


class Tokens:
    def __init__(self) -> None:
        self.values = iter(("session-token", "csrf-token"))

    def generate(self) -> str:
        return next(self.values)


class MemoryAdminStore:
    def __init__(self, operator: AdminOperatorRecord | None = None) -> None:
        self.operator = operator
        self.session: AdminSessionRecord | None = None
        self.audits: list[AdminAuditEvent] = []

    async def find_operator(self, username: str) -> AdminOperatorRecord | None:
        if self.operator and self.operator.username == username:
            return self.operator
        return None

    async def record_login_failure(self, **values: Any) -> AdminOperatorRecord:
        assert self.operator is not None
        now = values["now"]
        count = self.operator.failed_login_count
        if self.operator.locked_until is not None and self.operator.locked_until <= now:
            count = 0
        count += 1
        self.operator = replace(
            self.operator,
            failed_login_count=count,
            locked_until=values["lock_until"] if count >= values["failure_limit"] else None,
        )
        self.audits.append(values["audit"])
        return self.operator

    async def append_audit(self, event: AdminAuditEvent) -> None:
        self.audits.append(event)

    async def count_denied_sign_ins(self, **values: Any) -> tuple[int, int]:
        username = values["username"]
        ip_address = values["ip_address"]
        since = values["since"]
        matching = [
            event
            for event in self.audits
            if event.action == "admin.auth.sign_in"
            and event.outcome == "denied"
            and event.occurred_at is not None
            and event.occurred_at >= since
        ]
        return (
            sum(event.resource_id == username for event in matching),
            sum(event.actor.ip_address == ip_address for event in matching)
            if ip_address is not None
            else 0,
        )

    async def create_session_after_login(self, **values: Any):
        assert self.operator is not None
        self.operator = replace(
            self.operator,
            failed_login_count=0,
            locked_until=None,
            last_login_at=values["now"],
            password_hash=values["replacement_password_hash"] or self.operator.password_hash,
        )
        self.session = AdminSessionRecord(
            id="session-id",
            operator_id=self.operator.id,
            token_hash=values["token_hash"],
            csrf_token_hash=values["csrf_token_hash"],
            created_at=values["now"],
            last_seen_at=values["now"],
            idle_expires_at=values["idle_expires_at"],
            absolute_expires_at=values["absolute_expires_at"],
            ip_address=values["ip_address"],
            user_agent=values["user_agent"],
        )
        self.audits.append(values["audit"])
        return self.operator, self.session

    async def resolve_session(self, token_hash: str):
        if self.operator and self.session and self.session.token_hash == token_hash:
            return self.operator, self.session
        return None

    async def touch_session(self, **values: Any) -> AdminSessionRecord | None:
        if self.session is None or self.session.revoked_at is not None:
            return None
        self.session = replace(
            self.session,
            last_seen_at=values["now"],
            idle_expires_at=values["idle_expires_at"],
        )
        return self.session

    async def revoke_session(self, **values: Any) -> bool:
        if self.session is None:
            return False
        self.session = replace(self.session, revoked_at=values["now"])
        self.audits.append(values["audit"])
        return True


def operator(now: datetime) -> AdminOperatorRecord:
    return AdminOperatorRecord(
        id="operator-id",
        username="admin",
        display_name="Admin",
        password_hash="hashed:correct horse",
        status="active",
        password_changed_at=now,
        created_at=now,
        updated_at=now,
    )


def test_username_normalization_password_validation_and_audit_sanitization() -> None:
    assert normalize_admin_username("  Ad.Min@example.COM ") == "ad.min@example.com"
    with pytest.raises(AdminValidationError):
        normalize_admin_username("not allowed!")
    assert sanitize_audit_metadata(
        {
            "password": "raw",
            "sessionToken": "raw",
            "safe": {"credential_secret": "raw", "operation": "reset"},
        }
    ) == {"safe": {"operation": "reset"}}


def test_argon2id_verify_and_parameter_rehash() -> None:
    old = Argon2AdminPasswordHasher(time_cost=1, memory_cost=8_192, parallelism=1)
    current = Argon2AdminPasswordHasher(time_cost=2, memory_cost=8_192, parallelism=1)
    password_hash = old.hash("correct horse")

    assert password_hash.startswith("$argon2id$")
    assert old.verify(password_hash, "correct horse")
    assert not old.verify(password_hash, "incorrect")
    assert current.needs_rehash(password_hash)


@pytest.mark.asyncio
async def test_unknown_user_runs_dummy_verification_and_returns_generic_failure() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = MemoryAdminStore()
    hasher = FakeHasher()
    service = AdminAuthenticationService(store, hasher, Tokens(), FrozenClock(now))

    with pytest.raises(AdminAuthenticationError, match="Invalid username or password"):
        await service.sign_in(username="missing", password="guess")

    assert hasher.verifications == [("hashed:admin-dummy-verification-value", "guess")]
    assert store.audits[0].outcome == "denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", ["username", "ip"])
async def test_sign_in_throttle_uses_recent_denials_by_username_or_ip(dimension: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = MemoryAdminStore(operator(now))
    for index in range(10):
        store.audits.append(
            AdminAuditEvent(
                actor=AdminActorContext(
                    kind="anonymous",
                    identifier="admin" if dimension == "username" else f"other-{index}",
                    ip_address="203.0.113.10" if dimension == "ip" else f"203.0.113.{index}",
                ),
                action="admin.auth.sign_in",
                outcome="denied",
                resource_type="admin_operator",
                resource_id="admin" if dimension == "username" else f"other-{index}",
                occurred_at=now - timedelta(minutes=1),
            )
        )
    hasher = FakeHasher()
    service = AdminAuthenticationService(store, hasher, Tokens(), FrozenClock(now))

    with pytest.raises(AdminAuthenticationError, match="Invalid username or password"):
        await service.sign_in(
            username="admin", password="correct horse", ip_address="203.0.113.10"
        )

    assert hasher.verifications == [("hashed:admin-dummy-verification-value", "correct horse")]
    assert store.session is None
    assert store.audits[-1].metadata == {"reason": "throttled"}


@pytest.mark.asyncio
async def test_fifth_failure_locks_for_fifteen_minutes_then_unlocks() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(now)
    store = MemoryAdminStore(replace(operator(now), failed_login_count=4))
    service = AdminAuthenticationService(store, FakeHasher(), Tokens(), clock)

    with pytest.raises(AdminAuthenticationError):
        await service.sign_in(username="admin", password="wrong")
    assert store.operator is not None
    assert store.operator.failed_login_count == 5
    assert store.operator.locked_until == now + timedelta(minutes=15)

    clock.value = now + timedelta(minutes=15)
    result = await service.sign_in(username="admin", password="correct horse")
    assert result.operator.failed_login_count == 0
    assert result.operator.locked_until is None


@pytest.mark.asyncio
async def test_login_hashes_tokens_and_applies_fixed_idle_and_absolute_ttls() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = MemoryAdminStore(operator(now))
    service = AdminAuthenticationService(store, FakeHasher(), Tokens(), FrozenClock(now))

    created = await service.sign_in(username="ADMIN", password="correct horse")

    assert created.session_token == "session-token"
    assert created.csrf_token == "csrf-token"
    assert created.session.token_hash == hash_admin_token("session-token")
    assert created.session.csrf_token_hash == hash_admin_token("csrf-token")
    assert created.session.idle_expires_at == now + SESSION_IDLE_TTL
    assert created.session.absolute_expires_at == now + SESSION_ABSOLUTE_TTL


@pytest.mark.asyncio
async def test_session_idle_absolute_touch_and_revoke() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(now)
    store = MemoryAdminStore(operator(now))
    service = AdminAuthenticationService(store, FakeHasher(), Tokens(), clock)
    await service.sign_in(username="admin", password="correct horse")

    clock.value = now + timedelta(hours=23)
    authenticated = await service.verify_session("session-token")
    assert authenticated.actor.operator_id == "operator-id"
    assert store.session is not None
    assert store.session.idle_expires_at == now + timedelta(hours=47)

    await service.sign_out("session-token")
    with pytest.raises(AdminAuthenticationError):
        await service.verify_session("session-token")


class CapturingOperatorStore:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def bootstrap_operator(self, **values: Any) -> AdminOperatorRecord:
        self.values = values
        return operator(values["now"])


@pytest.mark.asyncio
async def test_operator_service_normalizes_and_hashes_before_persistence() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    store = CapturingOperatorStore()
    service = AdminOperatorService(store, FakeHasher(), FrozenClock(now))  # type: ignore[arg-type]

    await service.bootstrap_admin(
        username=" ADMIN ",
        display_name=" Admin User ",
        password="12345678",
        actor=AdminActorContext(kind="admin_cli", identifier="ops@host"),
    )

    assert store.values["username"] == "admin"
    assert store.values["display_name"] == "Admin User"
    assert store.values["password_hash"] == "hashed:12345678"
    assert store.values["audit"].actor.kind == "admin_cli"
