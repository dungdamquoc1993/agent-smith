from __future__ import annotations

import uuid
from os import getenv
from pathlib import Path
import sys

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.base import Base
from db.models.principal import Principal
from resources import PostgresResourceStore

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_app import server  # noqa: E402


@pytest.mark.asyncio
async def test_test_app_seed_principal_idempotent_when_database_is_configured(monkeypatch) -> None:
    database_url = getenv("AGENT_SMITH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENT_SMITH_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(server, "get_session_factory", lambda: factory)
    monkeypatch.setattr(server, "TEST_PRINCIPAL_DISPLAY_NAME", f"Test Principal {uuid.uuid4().hex}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        first = await server.ensure_test_principal()
        second = await server.ensure_test_principal()

        assert first.id == second.id
        async with factory() as db:
            rows = (
                await db.scalars(
                    select(Principal).where(
                        Principal.display_name == server.TEST_PRINCIPAL_DISPLAY_NAME
                    )
                )
            ).all()
            assert len(rows) == 1
    finally:
        async with factory() as db, db.begin():
            await db.execute(
                delete(Principal).where(Principal.display_name == server.TEST_PRINCIPAL_DISPLAY_NAME)
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_test_app_seed_resources_idempotent_when_database_is_configured(monkeypatch) -> None:
    database_url = getenv("AGENT_SMITH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENT_SMITH_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    agent_name = f"test_assistant_{uuid.uuid4().hex}"
    monkeypatch.setattr(server, "get_session_factory", lambda: factory)
    monkeypatch.setattr(server, "DEFAULT_AGENT_NAME", agent_name)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        first = await server.seed_resources()
        second = await server.seed_resources()

        assert first["status"] == "created"
        assert second["status"] == "exists"
        record = await PostgresResourceStore(factory).get_resource("agent_definition", agent_name)
        assert record is not None
        assert record.content["systemPrompt"]
        assert record.content["toolsAllow"] == []
    finally:
        store = PostgresResourceStore(factory)
        existing = await store.get_resource("agent_definition", agent_name)
        if existing is not None:
            await store.delete_resource("agent_definition", agent_name)
        await engine.dispose()


def test_json_dumps_serializes_pydantic_aliases() -> None:
    dumped = server._json_dumps({"value": uuid.UUID("00000000-0000-0000-0000-000000000001")})

    assert dumped == '{"value":"00000000-0000-0000-0000-000000000001"}'
