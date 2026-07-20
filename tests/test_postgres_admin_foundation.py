from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from os import getenv

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.app.ports.admin import (
    AdminActorContext,
    AdminAuditEvent,
    AdminBootstrapConflictError,
    LastActiveAdminError,
)
from agent_smith.infra.storage.postgres.adapters import (
    PostgresAdminAuditReader,
    PostgresAdminAuthenticationStore,
    PostgresAdminOperatorStore,
)
from agent_smith.infra.storage.postgres.database import Base
from agent_smith.infra.storage.postgres.models.admin import (
    AdminAuditEvent as DbAdminAuditEvent,
)
from agent_smith.infra.storage.postgres.models.admin import AdminOperator, AdminSession


def _audit(
    action: str,
    username: str,
    now: datetime,
    *,
    actor_operator_id: str | None = None,
) -> AdminAuditEvent:
    return AdminAuditEvent(
        actor=AdminActorContext(
            kind="admin_cli",
            identifier=f"test-{uuid.uuid4()}@host",
            operator_id=actor_operator_id,
        ),
        action=action,
        outcome="success",
        resource_type="admin_operator",
        resource_id=username,
        occurred_at=now,
    )


@pytest.mark.asyncio
async def test_concurrent_bootstrap_creates_exactly_one_admin_when_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = PostgresAdminOperatorStore(factory)
    now = datetime.now(UTC)
    usernames = [f"bootstrap-{uuid.uuid4().hex}", f"bootstrap-{uuid.uuid4().hex}"]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        results = await asyncio.gather(
            *[
                store.bootstrap_operator(
                    username=username,
                    display_name="Bootstrap Test",
                    password_hash="$argon2id$test-placeholder",
                    now=now,
                    audit=_audit("admin.operator.bootstrap", username, now),
                )
                for username in usernames
            ],
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, AdminBootstrapConflictError) for result in results) == 1
    finally:
        async with factory() as db, db.begin():
            await db.execute(
                delete(DbAdminAuditEvent).where(DbAdminAuditEvent.resource_id.in_(usernames))
            )
            await db.execute(delete(AdminOperator).where(AdminOperator.username.in_(usernames)))
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_mutations_sessions_and_audit_are_atomic_when_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")
    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    operators = PostgresAdminOperatorStore(factory)
    authentication = PostgresAdminAuthenticationStore(factory)
    audit_reader = PostgresAdminAuditReader(factory)
    now = datetime.now(UTC)
    username = f"admin-{uuid.uuid4().hex}"
    second_username = f"admin-{uuid.uuid4().hex}"
    created_ids: list[str] = []
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        first = await operators.bootstrap_operator(
            username=username,
            display_name="First",
            password_hash="old-hash",
            now=now,
            audit=_audit("admin.operator.bootstrap", username, now),
        )
        created_ids.append(first.id)
        second = await operators.add_operator(
            username=second_username,
            display_name="Second",
            password_hash="second-hash",
            now=now,
            audit=_audit("admin.operator.create", second_username, now),
        )
        created_ids.append(second.id)
        _, session = await authentication.create_session_after_login(
            operator_id=first.id,
            token_hash="a" * 64,
            csrf_token_hash="b" * 64,
            now=now,
            idle_expires_at=now + timedelta(days=1),
            absolute_expires_at=now + timedelta(days=7),
            ip_address=None,
            user_agent=None,
            replacement_password_hash=None,
            audit=AdminAuditEvent(
                actor=AdminActorContext(
                    kind="admin_operator", identifier=username, operator_id=first.id
                ),
                action="admin.auth.sign_in",
                outcome="success",
                occurred_at=now,
            ),
        )

        invalid_actor = str(uuid.uuid4())
        with pytest.raises(Exception):
            await operators.reset_password(
                username=username,
                password_hash="must-roll-back",
                now=now + timedelta(minutes=1),
                audit=_audit(
                    "admin.operator.password_reset",
                    username,
                    now + timedelta(minutes=1),
                    actor_operator_id=invalid_actor,
                ),
            )
        async with factory() as db:
            unchanged = await db.get(AdminOperator, uuid.UUID(first.id))
            active_session = await db.get(AdminSession, uuid.UUID(session.id))
            assert unchanged is not None and unchanged.password_hash == "old-hash"
            assert active_session is not None and active_session.revoked_at is None

        reset = await operators.reset_password(
            username=username,
            password_hash="new-hash",
            now=now + timedelta(minutes=2),
            audit=_audit("admin.operator.password_reset", username, now + timedelta(minutes=2)),
        )
        assert reset is not None and reset.password_hash == "new-hash"
        async with factory() as db:
            revoked = await db.get(AdminSession, uuid.UUID(session.id))
            assert revoked is not None and revoked.revoked_at is not None

        await operators.disable_operator(
            username=second_username,
            now=now + timedelta(minutes=3),
            audit=_audit("admin.operator.disable", second_username, now + timedelta(minutes=3)),
        )
        with pytest.raises(LastActiveAdminError):
            await operators.disable_operator(
                username=username,
                now=now + timedelta(minutes=4),
                audit=_audit("admin.operator.disable", username, now + timedelta(minutes=4)),
            )

        events = await audit_reader.list_audit_events(
            limit=2, resource_type="admin_operator", resource_id=username
        )
        assert len(events) == 2
        assert events[0].occurred_at is not None
        assert events[0].occurred_at >= events[1].occurred_at
        bounded = await audit_reader.list_audit_events(limit=10_000)
        assert len(bounded) <= 200
    finally:
        operator_uuids = [uuid.UUID(value) for value in created_ids]
        async with factory() as db, db.begin():
            if operator_uuids:
                await db.execute(
                    delete(DbAdminAuditEvent).where(
                        (DbAdminAuditEvent.actor_operator_id.in_(operator_uuids))
                        | (DbAdminAuditEvent.resource_id.in_([username, second_username]))
                    )
                )
                await db.execute(
                    delete(AdminSession).where(AdminSession.operator_id.in_(operator_uuids))
                )
                await db.execute(delete(AdminOperator).where(AdminOperator.id.in_(operator_uuids)))
        await engine.dispose()


def test_admin_metadata_contains_three_additive_tables() -> None:
    assert {
        "admin_operators",
        "admin_sessions",
        "admin_audit_events",
    }.issubset(Base.metadata.tables)
    assert len(Base.metadata.tables) == 21
