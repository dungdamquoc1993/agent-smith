"""Durable, secret-minimizing audit persistence for managed files."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.files import FileAuditEvent as AuditEvent
from agent_smith.app.ports.files import FileAuditUnavailable
from agent_smith.infra.storage.postgres.models.file import FileAuditEvent

AUDIT_DETAIL_ALLOWLIST = frozenset(
    {"mimeType", "declaredSize", "resultingStatus", "failureCode"}
)


def add_audit_event(db: AsyncSession, event: AuditEvent) -> None:
    """Stage one audit row in the caller's transaction."""
    db.add(
        FileAuditEvent(
            id=uuid.uuid4(),
            principal_id=_optional_uuid(event.principal_id),
            identity_provider_id=_optional_uuid(event.identity_provider_id),
            actor_subject=event.actor_subject[:512],
            file_id=_optional_uuid(event.file_id),
            action=event.action[:100],
            outcome=event.outcome[:100],
            correlation_id=event.correlation_id[:255] if event.correlation_id else None,
            details={
                key: value
                for key, value in event.details.items()
                if key in AUDIT_DETAIL_ALLOWLIST
            },
            occurred_at=event.occurred_at or datetime.now(UTC),
        )
    )


class PostgresFileAuditStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, events: list[AuditEvent]) -> None:
        if not events:
            return
        try:
            async with self._session_factory() as db, db.begin():
                for event in events:
                    add_audit_event(db, event)
                await db.flush()
        except (SQLAlchemyError, ValueError) as exc:
            raise FileAuditUnavailable("Unable to persist required file audit event") from exc

    async def purge_before(self, *, occurred_before: datetime, limit: int) -> int:
        if limit < 1:
            return 0
        async with self._session_factory() as db, db.begin():
            ids = (
                await db.scalars(
                    select(FileAuditEvent.id)
                    .where(FileAuditEvent.occurred_at < occurred_before)
                    .order_by(FileAuditEvent.occurred_at)
                    .limit(limit)
                )
            ).all()
            if not ids:
                return 0
            result = await db.execute(delete(FileAuditEvent).where(FileAuditEvent.id.in_(ids)))
            return int(result.rowcount or 0)


def _optional_uuid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value is not None else None
