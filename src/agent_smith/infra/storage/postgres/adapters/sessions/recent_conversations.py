"""Postgres recent-conversation capability."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.core.agent.harness.context_types import RecentConversationSnapshot
from agent_smith.infra.storage.postgres.adapters.sessions.catalog import (
    PostgresSessionCatalog,
)
from agent_smith.infra.storage.postgres.adapters.sessions.records import (
    metadata_from_row,
    uuid_value,
)
from agent_smith.infra.storage.postgres.models.sessions import (
    Session as DbSession,
    SessionKind as DbSessionKind,
)


class PostgresRecentConversationProvider:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_recent_conversations(
        self,
        *,
        principal_id: str,
        current_session_id: str,
        limit: int = 40,
    ) -> list[RecentConversationSnapshot]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(DbSession)
                    .where(
                        DbSession.principal_id == uuid_value(principal_id),
                        DbSession.kind == DbSessionKind.chat,
                        DbSession.id != uuid_value(current_session_id),
                    )
                    .order_by(DbSession.updated_at.desc(), DbSession.created_at.desc())
                    .limit(limit)
                )
            ).all()

        catalog = PostgresSessionCatalog(self._session_factory)
        snapshots: list[RecentConversationSnapshot] = []
        for row in rows:
            metadata = metadata_from_row(row)
            session = await catalog.open(metadata)
            context = await session.build_context()
            snapshots.append(
                RecentConversationSnapshot(
                    id=metadata.id,
                    title=metadata.title,
                    updated_at=row.updated_at.isoformat() if row.updated_at else None,
                    messages=context.messages,
                )
            )
        return snapshots


__all__ = ["PostgresRecentConversationProvider"]
