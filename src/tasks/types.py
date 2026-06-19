"""Public task runtime types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, Field

TaskKind: TypeAlias = Literal["agent", "shell", "remote_agent"]
TaskStatus: TypeAlias = Literal["pending", "running", "completed", "failed", "cancelled"]
TaskResult: TypeAlias = Any


class AbortSignal(Protocol):
    def is_set(self) -> bool: ...


class TaskErrorInfo(BaseModel):
    type: str
    message: str


class TaskRecord(BaseModel):
    id: str
    kind: TaskKind
    status: TaskStatus
    description: str
    created_at: datetime = Field(alias="createdAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    ended_at: datetime | None = Field(default=None, alias="endedAt")
    output_path: str | None = Field(default=None, alias="outputPath")
    output_bytes: int = Field(default=0, alias="outputBytes")
    result: TaskResult | None = None
    result_metadata: dict[str, Any] = Field(default_factory=dict, alias="resultMetadata")
    error: TaskErrorInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}


class TaskOutputSnapshot(BaseModel):
    task_id: str = Field(alias="taskId")
    text: str
    bytes: int
    truncated: bool = False

    model_config = {"populate_by_name": True}


AppendOutput = Callable[[str], Awaitable[None]]
SetResultMetadata = Callable[[Mapping[str, Any]], Awaitable[None]]


class TaskContext:
    def __init__(
        self,
        *,
        task_id: str,
        abort_signal: AbortSignal,
        append_output: AppendOutput,
        set_result_metadata: SetResultMetadata,
    ) -> None:
        self.task_id = task_id
        self.abort_signal = abort_signal
        self._append_output = append_output
        self._set_result_metadata = set_result_metadata

    async def append_output(self, text: str) -> None:
        await self._append_output(text)

    async def set_result_metadata(self, values: Mapping[str, Any]) -> None:
        await self._set_result_metadata(values)


TaskRun: TypeAlias = Callable[[TaskContext], Awaitable[TaskResult]]


class TaskRuntime(Protocol):
    async def spawn(
        self,
        *,
        kind: TaskKind,
        description: str,
        run: TaskRun,
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord: ...

    async def get(self, task_id: str) -> TaskRecord: ...

    async def list(self) -> list[TaskRecord]: ...

    async def wait(self, task_id: str, timeout_seconds: float | None = None) -> TaskRecord: ...

    async def stop(self, task_id: str, reason: str | None = None) -> TaskRecord: ...

    async def read_output(
        self,
        task_id: str,
        *,
        max_bytes: int | None = None,
    ) -> TaskOutputSnapshot: ...
