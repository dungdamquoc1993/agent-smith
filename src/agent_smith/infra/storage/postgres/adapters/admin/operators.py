"""Transactional Postgres adapter for admin operator administration."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.admin import (
    AdminAuditEvent,
    AdminBootstrapConflictError,
    AdminBootstrapRequiredError,
    AdminOperatorRecord,
    AdminStoreConflictError,
    LastActiveAdminError,
)
from agent_smith.infra.storage.postgres.adapters.admin.common import (
    add_audit_event,
    lock_admin_operator_set,
)
from agent_smith.infra.storage.postgres.adapters.admin.records import operator_record
from agent_smith.infra.storage.postgres.models.admin import (
    AdminOperator,
    AdminOperatorStatus,
    AdminSession,
)


class PostgresAdminOperatorStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def count_operators(self) -> int:
        async with self._session_factory() as db:
            return int(await db.scalar(select(func.count()).select_from(AdminOperator)) or 0)

    async def get_operator(self, username: str) -> AdminOperatorRecord | None:
        async with self._session_factory() as db:
            row = await db.scalar(select(AdminOperator).where(AdminOperator.username == username))
            return operator_record(row) if row is not None else None

    async def bootstrap_operator(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        now: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord:
        try:
            async with self._session_factory() as db, db.begin():
                await lock_admin_operator_set(db)
                count = await db.scalar(select(func.count()).select_from(AdminOperator))
                if count:
                    raise AdminBootstrapConflictError("An admin operator already exists.")
                row = self._new_operator(username, display_name, password_hash, now)
                db.add(row)
                add_audit_event(db, audit)
                await db.flush()
                await db.refresh(row)
                result = operator_record(row)
        except IntegrityError as exc:
            raise AdminStoreConflictError("Admin username already exists.") from exc
        return result

    async def add_operator(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        now: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord:
        try:
            async with self._session_factory() as db, db.begin():
                await lock_admin_operator_set(db)
                count = await db.scalar(select(func.count()).select_from(AdminOperator))
                if not count:
                    raise AdminBootstrapRequiredError(
                        "Use bootstrap-admin to create the first admin operator."
                    )
                row = self._new_operator(username, display_name, password_hash, now)
                db.add(row)
                add_audit_event(db, audit)
                await db.flush()
                await db.refresh(row)
                result = operator_record(row)
        except IntegrityError as exc:
            raise AdminStoreConflictError("Admin username already exists.") from exc
        return result

    async def reset_password(
        self,
        *,
        username: str,
        password_hash: str,
        now: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord | None:
        async with self._session_factory() as db, db.begin():
            row = await db.scalar(
                select(AdminOperator)
                .where(AdminOperator.username == username)
                .with_for_update()
            )
            if row is None:
                return None
            row.password_hash = password_hash
            row.password_changed_at = now
            row.failed_login_count = 0
            row.locked_until = None
            row.updated_at = now
            await db.execute(
                update(AdminSession)
                .where(AdminSession.operator_id == row.id, AdminSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            add_audit_event(db, audit)
            await db.flush()
            await db.refresh(row)
            return operator_record(row)

    async def disable_operator(
        self,
        *,
        username: str,
        now: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord | None:
        async with self._session_factory() as db, db.begin():
            await lock_admin_operator_set(db)
            row = await db.scalar(
                select(AdminOperator)
                .where(AdminOperator.username == username)
                .with_for_update()
            )
            if row is None:
                return None
            if row.status == AdminOperatorStatus.active:
                active_count = await db.scalar(
                    select(func.count())
                    .select_from(AdminOperator)
                    .where(AdminOperator.status == AdminOperatorStatus.active)
                )
                if int(active_count or 0) <= 1:
                    raise LastActiveAdminError("The last active admin cannot be disabled.")
                row.status = AdminOperatorStatus.disabled
                row.updated_at = now
            await db.execute(
                update(AdminSession)
                .where(AdminSession.operator_id == row.id, AdminSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            add_audit_event(db, audit)
            await db.flush()
            await db.refresh(row)
            return operator_record(row)

    @staticmethod
    def _new_operator(
        username: str, display_name: str, password_hash: str, now: datetime
    ) -> AdminOperator:
        return AdminOperator(
            id=uuid.uuid4(),
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            status=AdminOperatorStatus.active,
            failed_login_count=0,
            password_changed_at=now,
            created_at=now,
            updated_at=now,
        )
