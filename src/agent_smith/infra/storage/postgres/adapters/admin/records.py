"""Private SQLAlchemy-to-application record mapping for admin capabilities."""

from agent_smith.app.ports.admin import (
    AdminActorContext,
    AdminAuditEvent,
    AdminOperatorRecord,
    AdminSessionRecord,
)
from agent_smith.infra.storage.postgres.models.admin import (
    AdminAuditEvent as DbAdminAuditEvent,
)
from agent_smith.infra.storage.postgres.models.admin import AdminOperator, AdminSession


def operator_record(row: AdminOperator) -> AdminOperatorRecord:
    return AdminOperatorRecord(
        id=str(row.id),
        username=row.username,
        display_name=row.display_name,
        password_hash=row.password_hash,
        status=row.status.value,
        failed_login_count=row.failed_login_count,
        locked_until=row.locked_until,
        last_login_at=row.last_login_at,
        password_changed_at=row.password_changed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def session_record(row: AdminSession) -> AdminSessionRecord:
    return AdminSessionRecord(
        id=str(row.id),
        operator_id=str(row.operator_id),
        token_hash=row.token_hash,
        csrf_token_hash=row.csrf_token_hash,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
        revoked_at=row.revoked_at,
        ip_address=row.ip_address,
        user_agent=row.user_agent,
    )


def audit_record(row: DbAdminAuditEvent) -> AdminAuditEvent:
    return AdminAuditEvent(
        id=str(row.id),
        actor=AdminActorContext(
            kind=row.actor_kind,
            identifier=row.actor_identifier,
            operator_id=str(row.actor_operator_id) if row.actor_operator_id else None,
            request_id=row.request_id,
            ip_address=row.ip_address,
            user_agent=row.user_agent,
        ),
        action=row.action,
        outcome=row.outcome,  # type: ignore[arg-type]
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        metadata=dict(row.event_metadata),
        occurred_at=row.occurred_at,
    )
