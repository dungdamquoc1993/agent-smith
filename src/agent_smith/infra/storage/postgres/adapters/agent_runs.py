"""Postgres persistence for agent runs and logical LLM calls."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.core.runtime.run_store import (
    AgentRunFinish,
    AgentRunStart,
    AgentRunStoreError,
    LlmCallFinish,
    LlmCallStart,
)
from agent_smith.infra.storage.postgres.models.llm_calls import (
    AgentRun,
    AgentRunRecordingStatus,
    AgentRunStatus,
    LlmCall,
    LlmCallStatus,
)


class PostgresAgentRunStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start_run(self, run: AgentRunStart) -> None:
        values = {
            "id": uuid.UUID(run.id),
            "session_id": uuid.UUID(run.session_id),
            "principal_id": _required_uuid(run.principal_id, "principal_id"),
            "parent_run_id": uuid.UUID(run.parent_run_id) if run.parent_run_id else None,
            "agent_name": run.agent_name[:255],
            "flow": run.flow[:100],
            "status": AgentRunStatus.running,
            "recording_status": AgentRunRecordingStatus.pending,
            "correlation_id": run.correlation_id[:255] if run.correlation_id else None,
            "trace_id": run.trace_id[:255] if run.trace_id else None,
            "metadata": dict(run.metadata),
            "started_at": run.started_at or datetime.now(UTC),
        }
        await self._insert_idempotently(AgentRun, values, "agent run")

    async def finish_run(self, finish: AgentRunFinish) -> None:
        await self._update_required(
            update(AgentRun)
            .where(AgentRun.id == uuid.UUID(finish.run_id))
            .values(
                status=AgentRunStatus(finish.status),
                recording_status=AgentRunRecordingStatus(finish.recording_status),
                error_code=finish.error_code[:255] if finish.error_code else None,
                error_message=finish.error_message,
                completed_at=finish.completed_at or datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            "agent run",
        )

    async def start_call(self, call: LlmCallStart) -> None:
        values = {
            "id": uuid.UUID(call.id),
            "run_id": uuid.UUID(call.run_id),
            "session_id": uuid.UUID(call.session_id),
            "principal_id": _required_uuid(call.principal_id, "principal_id"),
            "sequence": call.sequence,
            "purpose": call.purpose[:100],
            "api": call.api[:100],
            "provider": call.provider[:100],
            "requested_model": call.requested_model[:255],
            "status": LlmCallStatus.started,
            "metadata": dict(call.metadata),
            "started_at": call.started_at or datetime.now(UTC),
        }
        await self._insert_idempotently(LlmCall, values, "LLM call")

    async def finish_call(self, finish: LlmCallFinish) -> None:
        usage = finish.usage
        cost = usage.cost
        values = {
            "status": LlmCallStatus(finish.status),
            "response_model": finish.response_model[:255] if finish.response_model else None,
            "provider_response_id": (
                finish.provider_response_id[:512] if finish.provider_response_id else None
            ),
            "stop_reason": finish.stop_reason[:50] if finish.stop_reason else None,
            "first_token_at": finish.first_token_at,
            "completed_at": finish.completed_at or datetime.now(UTC),
            "duration_ms": finish.duration_ms,
            "time_to_first_token_ms": finish.time_to_first_token_ms,
            "input_tokens": usage.input,
            "output_tokens": usage.output,
            "cache_read_tokens": usage.cache_read,
            "cache_write_tokens": usage.cache_write,
            "total_tokens": usage.total_tokens,
            "input_cost": _decimal(cost.input),
            "output_cost": _decimal(cost.output),
            "cache_read_cost": _decimal(cost.cache_read),
            "cache_write_cost": _decimal(cost.cache_write),
            "total_cost": _decimal(cost.total),
            "error_type": finish.error_type[:255] if finish.error_type else None,
            "error_code": finish.error_code[:255] if finish.error_code else None,
            "error_message": finish.error_message,
            "updated_at": datetime.now(UTC),
        }
        if finish.session_entry_id is not None:
            values["session_entry_id"] = uuid.UUID(finish.session_entry_id)
        await self._update_required(
            update(LlmCall)
            .where(LlmCall.id == uuid.UUID(finish.call_id))
            .values(**values),
            "LLM call",
        )

    async def link_call_session_entry(self, call_id: str, session_entry_id: str) -> None:
        await self._update_required(
            update(LlmCall)
            .where(LlmCall.id == uuid.UUID(call_id))
            .values(
                session_entry_id=uuid.UUID(session_entry_id),
                updated_at=datetime.now(UTC),
            ),
            "LLM call",
        )

    async def _insert_idempotently(self, model: type, values: dict, label: str) -> None:
        statement = insert(model).values(**values).on_conflict_do_nothing(index_elements=["id"])
        try:
            async with self._session_factory() as db, db.begin():
                await db.execute(statement)
        except (SQLAlchemyError, ValueError) as exc:
            raise AgentRunStoreError(f"Unable to persist {label}") from exc

    async def _update_required(self, statement, label: str) -> None:
        try:
            async with self._session_factory() as db, db.begin():
                result = await db.execute(statement)
                if result.rowcount != 1:
                    raise AgentRunStoreError(f"Unable to find {label} for update")
        except AgentRunStoreError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise AgentRunStoreError(f"Unable to persist {label}") from exc


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _required_uuid(value: str | None, field: str) -> uuid.UUID:
    if not value:
        raise AgentRunStoreError(f"{field} is required for Postgres agent recording")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise AgentRunStoreError(f"{field} must be a UUID") from exc
