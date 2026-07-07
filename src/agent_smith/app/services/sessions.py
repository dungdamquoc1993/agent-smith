"""Session use cases."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.infra.db.models.principal import Principal
from agent_smith.infra.db.models.session import Session as DbSession
from agent_smith.infra.persistence.postgres_sessions import PostgresSessionRepo


class SessionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        principal_display_name: str,
    ) -> None:
        self._session_factory = session_factory
        self.principal_display_name = principal_display_name

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def ensure_principal(self) -> Principal:
        async with self._session_factory() as db, db.begin():
            principal = (
                await db.scalars(
                    select(Principal)
                    .where(Principal.display_name == self.principal_display_name)
                    .order_by(Principal.created_at, Principal.id)
                )
            ).first()
            if principal is None:
                principal = Principal(
                    id=uuid.uuid4(),
                    display_name=self.principal_display_name,
                )
                db.add(principal)
                await db.flush()
            await db.refresh(principal)
            return principal

    async def list_sessions(self, limit: int = 25) -> list[dict[str, Any]]:
        principal = await self.ensure_principal()
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(DbSession)
                    .where(DbSession.principal_id == principal.id)
                    .order_by(DbSession.updated_at.desc(), DbSession.created_at.desc())
                    .limit(limit)
                )
            ).all()
            return [session_payload(row) for row in rows]

    async def create_session(self, title: str | None = None) -> dict[str, Any]:
        principal = await self.ensure_principal()
        repo = PostgresSessionRepo(self._session_factory)
        session = await repo.create(
            principal_id=str(principal.id),
            title=title or "Test chat",
            provenance={"source": "http_adapter", "trigger": "user"},
        )
        metadata = await session.get_metadata()
        return metadata.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def get_session_entries(self, session_id: str) -> dict[str, Any]:
        session_uuid = uuid.UUID(session_id)
        principal = await self.ensure_principal()
        async with self._session_factory() as db:
            row = await db.get(DbSession, session_uuid)
            if row is None or row.principal_id != principal.id:
                raise LookupError(f"Unknown test session: {session_id}")

        session = await PostgresSessionRepo(self._session_factory).open({"id": session_id})
        metadata = await session.get_metadata()
        entries = await session.get_entries()
        return {
            "session": metadata.model_dump(mode="json", by_alias=True, exclude_none=True),
            "entries": [
                entry.model_dump(mode="json", by_alias=True, exclude_none=True)
                for entry in entries
            ],
        }

    async def open_or_create_session(self, session_id: str | None):
        principal = await self.ensure_principal()
        repo = PostgresSessionRepo(self._session_factory)
        if session_id:
            session_uuid = uuid.UUID(session_id)
            async with self._session_factory() as db:
                row = await db.get(DbSession, session_uuid)
                if row is None or row.principal_id != principal.id:
                    raise LookupError(f"Unknown test session: {session_id}")
            return await repo.open({"id": session_id})
        return await repo.create(
            principal_id=str(principal.id),
            title="Test chat",
            provenance={"source": "http_adapter", "trigger": "user"},
        )


def principal_payload(row: Principal) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "displayName": row.display_name,
        "status": row.status.value if hasattr(row.status, "value") else row.status,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def session_payload(row: DbSession) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "principalId": str(row.principal_id),
        "title": row.title,
        "kind": row.kind.value if hasattr(row.kind, "value") else row.kind,
        "parentSessionId": str(row.parent_session_id) if row.parent_session_id else None,
        "agentName": row.agent_name,
        "originTaskId": row.origin_task_id,
        "currentLeafId": str(row.current_leaf_id) if row.current_leaf_id else None,
        "provenance": dict(row.provenance or {}),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }
