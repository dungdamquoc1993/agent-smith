"""Task runtime use cases."""

from __future__ import annotations

from agent_smith.core.tasks import TaskKind, TaskOutputSnapshot, TaskRecord, TaskRun, TaskRuntime


class TaskService:
    def __init__(self, runtime: TaskRuntime) -> None:
        self._runtime = runtime

    async def spawn(
        self,
        *,
        kind: TaskKind,
        description: str,
        run: TaskRun,
        metadata: dict | None = None,
    ) -> TaskRecord:
        return await self._runtime.spawn(
            kind=kind,
            description=description,
            run=run,
            metadata=metadata,
        )

    async def get(self, task_id: str) -> TaskRecord:
        return await self._runtime.get(task_id)

    async def list(self) -> list[TaskRecord]:
        return await self._runtime.list()

    async def wait(self, task_id: str, timeout_seconds: float | None = None) -> TaskRecord:
        return await self._runtime.wait(task_id, timeout_seconds=timeout_seconds)

    async def stop(self, task_id: str, reason: str | None = None) -> TaskRecord:
        return await self._runtime.stop(task_id, reason=reason)

    async def read_output(
        self,
        task_id: str,
        *,
        max_bytes: int | None = None,
    ) -> TaskOutputSnapshot:
        return await self._runtime.read_output(task_id, max_bytes=max_bytes)

