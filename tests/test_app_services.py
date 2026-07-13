from __future__ import annotations

import uuid
from os import getenv

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.app.services.resources import ResourceService
from agent_smith.app.services.sessions import SessionService
from agent_smith.app.services.tasks import TaskService
from agent_smith.core.llm.types import AssistantMessage, TextContent
from agent_smith.core.tasks import MemoryTaskRuntime, TaskContext
from agent_smith.infra.db.base import Base
from agent_smith.infra.db.models.principal import Principal
from agent_smith.infra.persistence.postgres_resources import PostgresResourceStore
from agent_smith.transports.http.sse import json_dumps


@pytest.mark.asyncio
async def test_session_service_seed_principal_idempotent_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")

    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    display_name = f"Test Principal {uuid.uuid4().hex}"
    service = SessionService(factory, principal_display_name=display_name)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        first = await service.ensure_principal()
        second = await service.ensure_principal()

        assert first.id == second.id
        async with factory() as db:
            rows = (
                await db.scalars(select(Principal).where(Principal.display_name == display_name))
            ).all()
            assert len(rows) == 1
    finally:
        async with factory() as db, db.begin():
            await db.execute(delete(Principal).where(Principal.display_name == display_name))
        await engine.dispose()


@pytest.mark.asyncio
async def test_resource_service_seed_default_agent_idempotent_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")

    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    agent_name = f"test_assistant_{uuid.uuid4().hex}"
    service = ResourceService(factory, default_agent_name=agent_name)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        first = await service.seed_default_agent()
        second = await service.seed_default_agent()

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


def test_http_json_dumps_serializes_pydantic_aliases() -> None:
    message = AssistantMessage(
        content=[TextContent(text="hello")],
        api="litellm",
        provider="openai",
        model="gpt-test",
        timestamp=1,
    )

    dumped = json_dumps(
        {
            "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
            "message": message,
        }
    )

    assert '"id":"00000000-0000-0000-0000-000000000001"' in dumped
    assert '"content":[{"type":"text","text":"hello"}]' in dumped


@pytest.mark.asyncio
async def test_task_service_spawns_waits_and_reads_output() -> None:
    service = TaskService(MemoryTaskRuntime())

    async def run(context: TaskContext) -> str:
        await context.append_output("hello")
        return "done"

    record = await service.spawn(kind="agent", description="demo", run=run)
    completed = await service.wait(record.id)
    output = await service.read_output(record.id)

    assert completed.status == "completed"
    assert completed.result == "done"
    assert output.text == "hello"
