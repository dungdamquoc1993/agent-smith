"""In-memory task runtime."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from agent_smith.core.tasks.errors import TaskAlreadyFinishedError, TaskTimeoutError, UnknownTaskError
from agent_smith.core.tasks.memory_task_output_store import MemoryTaskOutputStore
from agent_smith.core.tasks.types import (
    TaskContext,
    TaskErrorInfo,
    TaskKind,
    TaskOutputSnapshot,
    TaskOutputStore,
    TaskRecord,
    TaskRun,
)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryTaskRuntime:
    def __init__(self, output_store: TaskOutputStore | None = None) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        self._cancel_reasons: dict[str, str | None] = {}
        self._output_store = output_store or MemoryTaskOutputStore()
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        *,
        kind: TaskKind,
        description: str,
        run: TaskRun,
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        task_id = self._new_task_id()
        now = utc_now()
        abort_event = asyncio.Event()
        record = TaskRecord(
            id=task_id,
            kind=kind,
            status="running",
            description=description,
            created_at=now,
            started_at=now,
            metadata=dict(metadata or {}),
        )
        context = TaskContext(
            task_id=task_id,
            abort_signal=abort_event,
            append_output=lambda text: self._append_output(task_id, text),
            set_result_metadata=lambda values: self._set_result_metadata(task_id, values),
        )

        async with self._lock:
            self._records[task_id] = record
            self._abort_events[task_id] = abort_event

        task = asyncio.create_task(self._run_task(task_id, run, context, abort_event))
        async with self._lock:
            self._tasks[task_id] = task
            return self._snapshot(self._records[task_id])

    async def get(self, task_id: str) -> TaskRecord:
        async with self._lock:
            return self._snapshot(self._require_record(task_id))

    async def list(self) -> list[TaskRecord]:
        async with self._lock:
            return [self._snapshot(record) for record in self._records.values()]

    async def wait(self, task_id: str, timeout_seconds: float | None = None) -> TaskRecord:
        async with self._lock:
            record = self._require_record(task_id)
            if record.status in TERMINAL_STATUSES:
                return self._snapshot(record)
            task = self._tasks.get(task_id)

        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
            except TimeoutError as exc:
                raise TaskTimeoutError(task_id, timeout_seconds or 0) from exc
            except asyncio.CancelledError:
                pass

        async with self._lock:
            return self._snapshot(self._require_record(task_id))

    async def stop(self, task_id: str, reason: str | None = None) -> TaskRecord:
        async with self._lock:
            record = self._require_record(task_id)
            if record.status in TERMINAL_STATUSES:
                raise TaskAlreadyFinishedError(task_id, record.status)
            abort_event = self._abort_events[task_id]
            task = self._tasks.get(task_id)
            self._cancel_reasons[task_id] = reason

        abort_event.set()
        if task is not None:
            task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass

        await self._finalize_cancelled(task_id, reason)
        async with self._lock:
            return self._snapshot(self._require_record(task_id))

    async def read_output(
        self,
        task_id: str,
        *,
        max_bytes: int | None = None,
    ) -> TaskOutputSnapshot:
        async with self._lock:
            self._require_record(task_id)
        return await self._output_store.read(task_id, max_bytes=max_bytes)

    async def _run_task(
        self,
        task_id: str,
        run: TaskRun,
        context: TaskContext,
        abort_event: asyncio.Event,
    ) -> None:
        try:
            result = await run(context)
        except asyncio.CancelledError:
            await self._finalize_cancelled(task_id, self._cancel_reasons.get(task_id))
            raise
        except Exception as exc:
            if abort_event.is_set():
                await self._finalize_cancelled(task_id, self._cancel_reasons.get(task_id) or str(exc))
            else:
                await self._finalize_failed(task_id, exc)
        else:
            if abort_event.is_set():
                await self._finalize_cancelled(task_id, self._cancel_reasons.get(task_id))
            else:
                await self._finalize_completed(task_id, result)

    async def _append_output(self, task_id: str, text: str) -> None:
        await self._output_store.append(task_id, text)
        output_bytes = await self._output_store.size_bytes(task_id)
        async with self._lock:
            record = self._require_record(task_id)
            self._records[task_id] = record.model_copy(update={"output_bytes": output_bytes})

    async def _set_result_metadata(self, task_id: str, values: Mapping[str, Any]) -> None:
        async with self._lock:
            record = self._require_record(task_id)
            next_metadata = dict(record.result_metadata)
            next_metadata.update(dict(values))
            self._records[task_id] = record.model_copy(update={"result_metadata": next_metadata})

    async def _finalize_completed(self, task_id: str, result: Any) -> None:
        await self._finalize(
            task_id,
            status="completed",
            result=result,
            error=None,
        )

    async def _finalize_failed(self, task_id: str, exc: Exception) -> None:
        await self._finalize(
            task_id,
            status="failed",
            result=None,
            error=TaskErrorInfo(type=exc.__class__.__name__, message=str(exc)),
        )

    async def _finalize_cancelled(self, task_id: str, reason: str | None) -> None:
        await self._finalize(
            task_id,
            status="cancelled",
            result=None,
            error=TaskErrorInfo(type="cancelled", message=reason or "Task cancelled"),
        )

    async def _finalize(
        self,
        task_id: str,
        *,
        status: str,
        result: Any,
        error: TaskErrorInfo | None,
    ) -> None:
        output_bytes = await self._output_store.size_bytes(task_id)
        async with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status in TERMINAL_STATUSES:
                return
            self._records[task_id] = record.model_copy(
                update={
                    "status": status,
                    "ended_at": utc_now(),
                    "output_bytes": output_bytes,
                    "result": result,
                    "error": error,
                }
            )

    def _require_record(self, task_id: str) -> TaskRecord:
        record = self._records.get(task_id)
        if record is None:
            raise UnknownTaskError(task_id)
        return record

    def _snapshot(self, record: TaskRecord) -> TaskRecord:
        return record.model_copy(deep=True)

    def _new_task_id(self) -> str:
        return f"task_{uuid.uuid4().hex}"
