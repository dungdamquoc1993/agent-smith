"""Transactional Postgres adapter for admin authentication and sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.admin import (
    AdminAuditEvent,
    AdminOperatorRecord,
    AdminSessionRecord,
)
from agent_smith.infra.storage.postgres.adapters.admin.common import add_audit_event
from agent_smith.infra.storage.postgres.adapters.admin.records import (
    operator_record,
    session_record,
)
from agent_smith.infra.storage.postgres.models.admin import (
    AdminOperator,
    AdminOperatorStatus,
    AdminSession,
)
from agent_smith.infra.storage.postgres.models.admin import AdminAuditEvent as DbAdminAuditEvent


class PostgresAdminAuthenticationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_operator(self, username: str) -> AdminOperatorRecord | None:
        async with self._session_factory() as db:
            row = await db.scalar(select(AdminOperator).where(AdminOperator.username == username))
            return operator_record(row) if row is not None else None

    async def record_login_failure(
        self,
        *,
        operator_id: str,
        now: datetime,
        failure_limit: int,
        lock_until: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord:
        async with self._session_factory() as db, db.begin():
            row = await db.scalar(
                select(AdminOperator)
                .where(AdminOperator.id == uuid.UUID(operator_id))
                .with_for_update()
            )
            if row is None:
                raise LookupError("Admin operator disappeared during authentication.")
            if row.locked_until is not None and row.locked_until <= now:
                row.failed_login_count = 0
                row.locked_until = None
            row.failed_login_count += 1
            if row.failed_login_count >= failure_limit:
                row.locked_until = lock_until
            row.updated_at = now
            add_audit_event(db, audit)
            await db.flush()
            await db.refresh(row)
            return operator_record(row)

    async def append_audit(self, event: AdminAuditEvent) -> None:
        async with self._session_factory() as db, db.begin():
            add_audit_event(db, event)
            await db.flush()

    async def count_denied_sign_ins(
        self,
        *,
        username: str,
        ip_address: str | None,
        since: datetime,
    ) -> tuple[int, int]:
        base = (
            DbAdminAuditEvent.action == "admin.auth.sign_in",
            DbAdminAuditEvent.outcome == "denied",
            DbAdminAuditEvent.occurred_at >= since,
        )
        async with self._session_factory() as db:
            username_count = await db.scalar(
                select(func.count()).select_from(DbAdminAuditEvent).where(
                    *base, DbAdminAuditEvent.resource_id == username
                )
            )
            ip_count = 0
            if ip_address is not None:
                ip_count = await db.scalar(
                    select(func.count()).select_from(DbAdminAuditEvent).where(
                        *base, DbAdminAuditEvent.ip_address == ip_address
                    )
                )
            return int(username_count or 0), int(ip_count or 0)

    async def create_session_after_login(
        self,
        *,
        operator_id: str,
        token_hash: str,
        csrf_token_hash: str,
        now: datetime,
        idle_expires_at: datetime,
        absolute_expires_at: datetime,
        ip_address: str | None,
        user_agent: str | None,
        replacement_password_hash: str | None,
        audit: AdminAuditEvent,
    ) -> tuple[AdminOperatorRecord, AdminSessionRecord]:
        async with self._session_factory() as db, db.begin():
            operator = await db.scalar(
                select(AdminOperator)
                .where(AdminOperator.id == uuid.UUID(operator_id))
                .with_for_update()
            )
            if operator is None or operator.status != AdminOperatorStatus.active:
                raise LookupError("Active admin operator was not found.")
            operator.failed_login_count = 0
            operator.locked_until = None
            operator.last_login_at = now
            operator.updated_at = now
            if replacement_password_hash is not None:
                operator.password_hash = replacement_password_hash
            session = AdminSession(
                id=uuid.uuid4(),
                operator_id=operator.id,
                token_hash=token_hash,
                csrf_token_hash=csrf_token_hash,
                created_at=now,
                last_seen_at=now,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=absolute_expires_at,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.add(session)
            add_audit_event(db, audit)
            await db.flush()
            await db.refresh(operator)
            await db.refresh(session)
            return operator_record(operator), session_record(session)

    async def resolve_session(
        self, token_hash: str
    ) -> tuple[AdminOperatorRecord, AdminSessionRecord] | None:
        async with self._session_factory() as db:
            result = (
                await db.execute(
                    select(AdminOperator, AdminSession)
                    .join(AdminSession, AdminSession.operator_id == AdminOperator.id)
                    .where(AdminSession.token_hash == token_hash)
                )
            ).one_or_none()
            if result is None:
                return None
            operator, session = result
            return operator_record(operator), session_record(session)

    async def touch_session(
        self, *, session_id: str, now: datetime, idle_expires_at: datetime
    ) -> AdminSessionRecord | None:
        async with self._session_factory() as db, db.begin():
            row = await db.scalar(
                select(AdminSession)
                .where(
                    AdminSession.id == uuid.UUID(session_id),
                    AdminSession.revoked_at.is_(None),
                    AdminSession.idle_expires_at > now,
                    AdminSession.absolute_expires_at > now,
                )
                .with_for_update()
            )
            if row is None:
                return None
            row.last_seen_at = now
            row.idle_expires_at = min(idle_expires_at, row.absolute_expires_at)
            await db.flush()
            await db.refresh(row)
            return session_record(row)

    async def revoke_session(
        self, *, session_id: str, now: datetime, audit: AdminAuditEvent
    ) -> bool:
        async with self._session_factory() as db, db.begin():
            row = await db.scalar(
                select(AdminSession)
                .where(AdminSession.id == uuid.UUID(session_id))
                .with_for_update()
            )
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = now
            add_audit_event(db, audit)
            await db.flush()
            return True
