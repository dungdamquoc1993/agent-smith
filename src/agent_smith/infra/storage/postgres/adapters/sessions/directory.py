"""Postgres principal-session directory capability."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.sessions import PrincipalRecord, SessionRecord
from agent_smith.infra.storage.postgres.adapters.sessions.records import (
    principal_record,
    session_record,
    uuid_value,
)
from agent_smith.infra.storage.postgres.models.principals import Principal
from agent_smith.infra.storage.postgres.models.sessions import Session as DbSession


class PostgresPrincipalSessionDirectory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_principal(self, display_name: str) -> PrincipalRecord:
        async with self._session_factory() as db, db.begin():
            principal = (
                await db.scalars(
                    select(Principal)
                    .where(Principal.display_name == display_name)
                    .order_by(Principal.created_at, Principal.id)
                )
            ).first()
            if principal is None:
                principal = Principal(id=uuid.uuid4(), display_name=display_name)
                db.add(principal)
                await db.flush()
            await db.refresh(principal)
            return principal_record(principal)

    async def list_sessions(
        self,
        *,
        principal_id: str,
        limit: int = 25,
    ) -> list[SessionRecord]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(DbSession)
                    .where(DbSession.principal_id == uuid_value(principal_id))
                    .order_by(DbSession.updated_at.desc(), DbSession.created_at.desc())
                    .limit(limit)
                )
            ).all()
            return [session_record(row) for row in rows]

    async def session_belongs_to(self, *, session_id: str, principal_id: str) -> bool:
        async with self._session_factory() as db:
            row = await db.get(DbSession, uuid_value(session_id))
            return row is not None and row.principal_id == uuid_value(principal_id)


__all__ = ["PostgresPrincipalSessionDirectory"]
