"""Bounded Postgres reader for immutable admin audit events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.admin import AdminAuditEvent
from agent_smith.infra.storage.postgres.adapters.admin.records import audit_record
from agent_smith.infra.storage.postgres.models.admin import (
    AdminAuditEvent as DbAdminAuditEvent,
)

MAX_AUDIT_PAGE_SIZE = 200


class PostgresAdminAuditReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_audit_events(
        self,
        *,
        limit: int = 50,
        before: datetime | None = None,
        before_id: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        actor_operator_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> list[AdminAuditEvent]:
        conditions: list[Any] = []
        if before is not None and before_id is not None:
            conditions.append(
                or_(
                    DbAdminAuditEvent.occurred_at < before,
                    and_(
                        DbAdminAuditEvent.occurred_at == before,
                        DbAdminAuditEvent.id < uuid.UUID(before_id),
                    ),
                )
            )
        elif before is not None:
            conditions.append(DbAdminAuditEvent.occurred_at < before)
        if action is not None:
            conditions.append(DbAdminAuditEvent.action == action)
        if outcome is not None:
            conditions.append(DbAdminAuditEvent.outcome == outcome)
        if actor_operator_id is not None:
            conditions.append(
                DbAdminAuditEvent.actor_operator_id == uuid.UUID(actor_operator_id)
            )
        if resource_type is not None:
            conditions.append(DbAdminAuditEvent.resource_type == resource_type)
        if resource_id is not None:
            conditions.append(DbAdminAuditEvent.resource_id == resource_id)
        # The HTTP adapter requests one look-ahead row only for a public max-size page.
        page_size = MAX_AUDIT_PAGE_SIZE + 1 if limit == MAX_AUDIT_PAGE_SIZE + 1 else max(
            1, min(limit, MAX_AUDIT_PAGE_SIZE)
        )
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(DbAdminAuditEvent)
                    .where(*conditions)
                    .order_by(
                        DbAdminAuditEvent.occurred_at.desc(),
                        DbAdminAuditEvent.id.desc(),
                    )
                    .limit(page_size)
                )
            ).all()
            return [audit_record(row) for row in rows]
