from __future__ import annotations

import uuid
from datetime import UTC, datetime
from os import getenv

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.core.llm.types import Usage, UsageCost
from agent_smith.core.runtime import AgentRunFinish, AgentRunStart, LlmCallFinish, LlmCallStart
from agent_smith.infra.storage.postgres.adapters import PostgresAgentRunStore
from agent_smith.infra.storage.postgres import models as postgres_models
from agent_smith.infra.storage.postgres.database import Base
from agent_smith.infra.storage.postgres.models.llm_calls import AgentRun, LlmCall
from agent_smith.infra.storage.postgres.models.principals import Principal
from agent_smith.infra.storage.postgres.models.sessions import Session, SessionEntry, SessionEntryType


def test_agent_run_and_llm_call_models_are_exported() -> None:
    assert postgres_models.AgentRun.__tablename__ == "agent_runs"
    assert postgres_models.LlmCall.__tablename__ == "llm_calls"
    assert postgres_models.AgentRunStatus.running.value == "running"
    assert {status.value for status in postgres_models.AgentRunRecordingStatus} == {
        "pending",
        "complete",
        "degraded",
    }
    assert postgres_models.LlmCallStatus.succeeded.value == "succeeded"


def test_agent_runs_schema_tracks_run_lifecycle() -> None:
    table = Base.metadata.tables["agent_runs"]

    assert set(table.columns.keys()) == {
        "id",
        "session_id",
        "principal_id",
        "parent_run_id",
        "agent_name",
        "flow",
        "status",
        "recording_status",
        "correlation_id",
        "trace_id",
        "error_code",
        "error_message",
        "metadata",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    }
    assert {index.name for index in table.indexes} == {
        "ix_agent_runs_session_started",
        "ix_agent_runs_parent_run_id",
        "ix_agent_runs_principal_started",
        "ix_agent_runs_status_started",
        "ix_agent_runs_correlation_id",
    }
    assert {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {"ck_agent_runs_completed_after_started"}
    parent_fk = next(iter(table.columns.parent_run_id.foreign_keys))
    assert table.columns.parent_run_id.nullable is True
    assert parent_fk.target_fullname == "agent_runs.id"
    assert parent_fk.ondelete == "SET NULL"
    assert str(table.columns.recording_status.server_default.arg) == "pending"


def test_llm_calls_schema_tracks_usage_cost_and_timing() -> None:
    table = Base.metadata.tables["llm_calls"]

    assert {
        "run_id",
        "session_id",
        "principal_id",
        "session_entry_id",
        "sequence",
        "purpose",
        "provider",
        "requested_model",
        "response_model",
        "provider_response_id",
        "status",
        "stop_reason",
        "started_at",
        "first_token_at",
        "completed_at",
        "duration_ms",
        "time_to_first_token_ms",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        "input_cost",
        "output_cost",
        "cache_read_cost",
        "cache_write_cost",
        "total_cost",
        "attempt_count",
        "request_fingerprint",
    }.issubset(table.columns.keys())
    assert {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {"uq_llm_calls_run_sequence", "uq_llm_calls_session_entry"}


@pytest.mark.asyncio
async def test_postgres_agent_run_store_is_idempotent_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")

    engine = create_async_engine(postgres_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = PostgresAgentRunStore(factory)
    principal_id = uuid.uuid4()
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    call_id = uuid.uuid4()
    entry_id = uuid.uuid4()
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db, db.begin():
            db.add(Principal(id=principal_id, display_name="Agent recording test"))
            db.add(Session(id=session_id, principal_id=principal_id))
            db.add(
                SessionEntry(
                    id=entry_id,
                    session_id=session_id,
                    principal_id=principal_id,
                    type=SessionEntryType.message,
                    payload={},
                )
            )

        run = AgentRunStart(
            id=str(run_id),
            session_id=str(session_id),
            principal_id=str(principal_id),
            agent_name="assistant",
            flow="test",
            started_at=now,
        )
        call = LlmCallStart(
            id=str(call_id),
            run_id=str(run_id),
            session_id=str(session_id),
            principal_id=str(principal_id),
            sequence=1,
            purpose="agent_turn",
            api="litellm",
            provider="openai",
            requested_model="gpt-test",
            started_at=now,
        )
        await store.start_run(run)
        await store.start_run(run)
        await store.start_call(call)
        await store.start_call(call)
        finish_call = LlmCallFinish(
            call_id=str(call_id),
            status="succeeded",
            session_entry_id=str(entry_id),
            usage=Usage(
                input=5,
                output=2,
                totalTokens=7,
                cost=UsageCost(total=0.01),
            ),
            completed_at=now,
        )
        await store.finish_call(finish_call)
        await store.finish_call(finish_call)
        finish_run = AgentRunFinish(
            run_id=str(run_id),
            status="completed",
            recording_status="complete",
            completed_at=now,
        )
        await store.finish_run(finish_run)
        await store.finish_run(finish_run)

        async with factory() as db:
            assert await db.scalar(
                select(func.count()).select_from(AgentRun).where(AgentRun.id == run_id)
            ) == 1
            assert await db.scalar(
                select(func.count()).select_from(LlmCall).where(LlmCall.id == call_id)
            ) == 1
            persisted = await db.scalar(select(LlmCall).where(LlmCall.id == call_id))
            assert persisted is not None
            assert persisted.total_tokens == 7
            assert persisted.session_entry_id == entry_id
    finally:
        async with factory() as db, db.begin():
            await db.execute(delete(AgentRun).where(AgentRun.id == run_id))
            await db.execute(delete(Session).where(Session.id == session_id))
            await db.execute(delete(Principal).where(Principal.id == principal_id))
        await engine.dispose()
