"""Explicit Postgres engine, pool, and async session lifecycle."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class PostgresRuntime:
    """Process-owned Postgres resources with an explicit shutdown boundary."""

    def __init__(self, postgres_url: str, *, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(postgres_url, echo=echo)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self._closed = False

    async def check(self) -> None:
        async with self.engine.connect() as connection:
            await connection.exec_driver_sql("select 1")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.engine.dispose()
