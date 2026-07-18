"""Session capability-private row mappers."""

import uuid
from typing import Any

from agent_smith.app.ports.sessions import PrincipalRecord, SessionRecord
from agent_smith.core.agent.harness.session.types import SessionMetadata
from agent_smith.infra.storage.postgres.models.principals import Principal
from agent_smith.infra.storage.postgres.models.sessions import (
    Session as DbSession,
    SessionEntry as DbSessionEntry,
)


def enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def uuid_value(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def metadata_from_row(row: DbSession) -> SessionMetadata:
    return SessionMetadata(
        id=str(row.id),
        principal_id=str(row.principal_id),
        title=row.title,
        kind=enum_value(row.kind),
        parent_session_id=str(row.parent_session_id) if row.parent_session_id else None,
        agent_name=row.agent_name,
        origin_task_id=row.origin_task_id,
        provenance=dict(row.provenance or {}),
    )


def principal_record(row: Principal) -> PrincipalRecord:
    return PrincipalRecord(
        id=str(row.id),
        display_name=row.display_name,
        status=enum_value(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def session_record(row: DbSession) -> SessionRecord:
    return SessionRecord(
        id=str(row.id),
        principal_id=str(row.principal_id),
        title=row.title,
        kind=enum_value(row.kind),
        parent_session_id=str(row.parent_session_id) if row.parent_session_id else None,
        agent_name=row.agent_name,
        origin_task_id=row.origin_task_id,
        current_leaf_id=str(row.current_leaf_id) if row.current_leaf_id else None,
        provenance=dict(row.provenance or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def rows_to_branch(
    entries: list[DbSessionEntry],
    leaf_id: uuid.UUID | None,
) -> list[DbSessionEntry]:
    if leaf_id is None:
        return []
    by_id = {entry.id: entry for entry in entries}
    path: list[DbSessionEntry] = []
    current_id: uuid.UUID | None = leaf_id
    while current_id is not None:
        entry = by_id.get(current_id)
        if entry is None:
            raise ValueError(f"Entry {current_id} not found in source session")
        path.append(entry)
        current_id = entry.parent_id
    return list(reversed(path))
