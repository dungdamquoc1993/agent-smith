"""Authenticated, filtered, cursor-paginated admin audit API."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from http import HTTPStatus

from fastapi import APIRouter, Depends, Query

from agent_smith.app.ports.admin import AuthenticatedAdminSession
from agent_smith.bootstrap.admin_http import AdminHttpContainer
from agent_smith.transports.admin_http.security import get_container, require_admin_session
from agent_smith.transports.shared_http import AgentSmithHttpError, json_response

router = APIRouter(prefix="/api")


@router.get("/audit-events")
async def list_audit_events(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    actor_operator_id: str | None = Query(default=None, alias="actorOperatorId"),
    resource_type: str | None = Query(default=None, alias="resourceType"),
    resource_id: str | None = Query(default=None, alias="resourceId"),
    resource: str | None = None,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_session),
    container: AdminHttpContainer = Depends(get_container),
):
    del authenticated
    if outcome is not None and outcome not in {"success", "denied", "failed"}:
        raise AgentSmithHttpError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_filter", "Invalid outcome.")
    before, before_id = _decode_cursor(cursor)
    try:
        events = await container.audit_reader.list_audit_events(
            limit=limit + 1,
            before=before,
            before_id=before_id,
            action=action,
            outcome=outcome,
            actor_operator_id=actor_operator_id,
            resource_type=resource_type,
            resource_id=resource_id or resource,
        )
    except ValueError as exc:
        raise AgentSmithHttpError(
            HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_filter", "Invalid operator id."
        ) from exc
    page = events[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(events) > limit and page else None
    return json_response(
        {
            "auditEvents": [_payload(event) for event in page],
            "nextCursor": next_cursor,
        }
    )


def _payload(event: object) -> dict[str, object]:
    actor = getattr(event, "actor")
    return {
        "id": getattr(event, "id"),
        "action": getattr(event, "action"),
        "outcome": getattr(event, "outcome"),
        "actor": {
            "kind": actor.kind,
            "identifier": actor.identifier,
            "operatorId": actor.operator_id,
            "requestId": actor.request_id,
            "ipAddress": actor.ip_address,
        },
        "resourceType": getattr(event, "resource_type"),
        "resourceId": getattr(event, "resource_id"),
        "metadata": getattr(event, "metadata"),
        "occurredAt": getattr(event, "occurred_at"),
    }


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        occurred_at = datetime.fromisoformat(data["occurredAt"])
        if occurred_at.tzinfo is None:
            raise ValueError("cursor timestamp must include a timezone")
        return occurred_at, str(uuid.UUID(data["id"]))
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentSmithHttpError(
            HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_cursor", "Invalid pagination cursor."
        ) from exc


def _encode_cursor(event: object) -> str | None:
    occurred_at = getattr(event, "occurred_at")
    event_id = getattr(event, "id")
    if occurred_at is None or event_id is None:
        return None
    raw = json.dumps(
        {"occurredAt": occurred_at.isoformat(), "id": event_id}, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
