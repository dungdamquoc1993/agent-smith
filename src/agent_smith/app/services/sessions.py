"""Session use cases."""

from __future__ import annotations

from typing import Any

from agent_smith.app.ports.sessions import (
    PrincipalRecord,
    PrincipalSessionDirectory,
    SessionCatalog,
    SessionRecord,
)


class SessionService:
    def __init__(
        self,
        directory: PrincipalSessionDirectory,
        catalog: SessionCatalog,
        *,
        principal_display_name: str,
    ) -> None:
        self._directory = directory
        self._catalog = catalog
        self.principal_display_name = principal_display_name

    async def ensure_principal(self) -> PrincipalRecord:
        return await self._directory.ensure_principal(self.principal_display_name)

    async def list_sessions(self, limit: int = 25) -> list[dict[str, Any]]:
        principal = await self.ensure_principal()
        rows = await self._directory.list_sessions(principal_id=principal.id, limit=limit)
        return [session_payload(row) for row in rows]

    async def create_session(self, title: str | None = None) -> dict[str, Any]:
        principal = await self.ensure_principal()
        session = await self._catalog.create(
            principal_id=principal.id,
            title=title or "Test chat",
            provenance={"source": "http_adapter", "trigger": "user"},
        )
        metadata = await session.get_metadata()
        return metadata.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def get_session_entries(self, session_id: str) -> dict[str, Any]:
        principal = await self.ensure_principal()
        if not await self._directory.session_belongs_to(
            session_id=session_id,
            principal_id=principal.id,
        ):
            raise LookupError(f"Unknown test session: {session_id}")

        session = await self._catalog.open({"id": session_id})
        metadata = await session.get_metadata()
        entries = await session.get_entries()
        return {
            "session": metadata.model_dump(mode="json", by_alias=True, exclude_none=True),
            "entries": [
                entry.model_dump(mode="json", by_alias=True, exclude_none=True) for entry in entries
            ],
        }

    async def open_or_create_session(self, session_id: str | None):
        principal = await self.ensure_principal()
        return await self.open_or_create_session_for_principal(
            principal_id=str(principal.id),
            session_id=session_id,
            provenance={"source": "http_adapter", "trigger": "user"},
        )

    async def open_or_create_session_for_principal(
        self,
        *,
        principal_id: str,
        session_id: str | None,
        provenance: dict[str, Any] | None = None,
    ):
        if session_id:
            if not await self._directory.session_belongs_to(
                session_id=session_id,
                principal_id=principal_id,
            ):
                raise LookupError(f"Unknown test session: {session_id}")
            return await self._catalog.open({"id": session_id})
        return await self._catalog.create(
            principal_id=principal_id,
            title="Test chat",
            provenance=dict(provenance or {}),
        )


def principal_payload(row: PrincipalRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "displayName": row.display_name,
        "status": row.status,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def session_payload(row: SessionRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "principalId": row.principal_id,
        "title": row.title,
        "kind": row.kind,
        "parentSessionId": row.parent_session_id,
        "agentName": row.agent_name,
        "originTaskId": row.origin_task_id,
        "currentLeafId": row.current_leaf_id,
        "provenance": dict(row.provenance or {}),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }
