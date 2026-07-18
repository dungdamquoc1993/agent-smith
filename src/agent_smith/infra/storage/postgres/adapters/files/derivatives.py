"""Postgres read adapter for successful file derivatives."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_smith.app.ports.document_processing import DerivativeRecord
from agent_smith.infra.storage.postgres.adapters.files.records import derivative_record
from agent_smith.infra.storage.postgres.models.file_processing import (
    FileDerivative,
    FileProcessingJob,
    ProcessingJobStatus,
)


class PostgresFileDerivativeReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_derivatives(
        self, *, file_id: str, kinds: tuple[str, ...] | None = None
    ) -> list[DerivativeRecord]:
        conditions: list[Any] = [
            FileDerivative.file_id == _uuid(file_id),
            FileProcessingJob.status == ProcessingJobStatus.succeeded,
        ]
        if kinds:
            conditions.append(FileDerivative.kind.in_(kinds))
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(FileDerivative)
                    .join(FileProcessingJob, FileProcessingJob.id == FileDerivative.processing_job_id)
                    .where(*conditions)
                    .order_by(FileDerivative.created_at, FileDerivative.id)
                )
            ).all()
            return [derivative_record(row) for row in rows]


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Invalid UUID") from exc
