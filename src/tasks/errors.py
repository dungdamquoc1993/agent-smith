"""Errors raised by task runtime implementations."""

from __future__ import annotations


class TaskRuntimeError(Exception):
    """Base class for task runtime failures."""


class UnknownTaskError(TaskRuntimeError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Unknown task: {task_id}")


class TaskAlreadyFinishedError(TaskRuntimeError):
    def __init__(self, task_id: str, status: str) -> None:
        self.task_id = task_id
        self.status = status
        super().__init__(f"Task {task_id} already finished with status: {status}")


class TaskTimeoutError(TaskRuntimeError):
    def __init__(self, task_id: str, timeout_seconds: float) -> None:
        self.task_id = task_id
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Timed out waiting for task {task_id} after {timeout_seconds:g}s")
