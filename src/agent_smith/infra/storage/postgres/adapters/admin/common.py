"""Shared transaction-local helpers for Postgres admin adapters."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_smith.app.ports.admin import AdminAuditEvent
from agent_smith.app.services.admin import sanitize_audit_metadata
from agent_smith.infra.storage.postgres.models.admin import (
    AdminAuditEvent as DbAdminAuditEvent,
)

ADMIN_OPERATOR_ADVISORY_LOCK = 5_312_024_041


async def lock_admin_operator_set(db: AsyncSession) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": ADMIN_OPERATOR_ADVISORY_LOCK},
    )


def add_audit_event(db: AsyncSession, event: AdminAuditEvent) -> None:
    actor = event.actor
    db.add(
        DbAdminAuditEvent(
            id=uuid.UUID(event.id) if event.id else uuid.uuid4(),
            actor_kind=actor.kind,
            actor_operator_id=uuid.UUID(actor.operator_id) if actor.operator_id else None,
            actor_identifier=actor.identifier,
            action=event.action,
            outcome=event.outcome,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            request_id=actor.request_id,
            ip_address=actor.ip_address,
            user_agent=actor.user_agent,
            event_metadata=sanitize_audit_metadata(event.metadata),
            occurred_at=event.occurred_at or datetime.now(UTC),
        )
    )
