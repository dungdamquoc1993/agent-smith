"""Task output storage."""

from __future__ import annotations

import asyncio
from typing import Protocol

from tasks.types import TaskOutputSnapshot


class TaskOutputStore(Protocol):
    async def append(self, task_id: str, text: str) -> None: ...

    async def read(self, task_id: str, *, max_bytes: int | None = None) -> TaskOutputSnapshot: ...

    async def clear(self, task_id: str) -> None: ...

    async def size_bytes(self, task_id: str) -> int: ...


class MemoryTaskOutputStore:
    def __init__(self) -> None:
        self._chunks: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def append(self, task_id: str, text: str) -> None:
        async with self._lock:
            self._chunks.setdefault(task_id, []).append(text)

    async def read(self, task_id: str, *, max_bytes: int | None = None) -> TaskOutputSnapshot:
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes must be greater than or equal to 0")

        async with self._lock:
            text = "".join(self._chunks.get(task_id, []))

        encoded = text.encode("utf-8")
        if max_bytes is not None and len(encoded) > max_bytes:
            if max_bytes == 0:
                return TaskOutputSnapshot(
                    task_id=task_id,
                    text="",
                    bytes=len(encoded),
                    truncated=True,
                )
            tail = encoded[-max_bytes:]
            text = tail.decode("utf-8", errors="ignore")
            return TaskOutputSnapshot(
                task_id=task_id,
                text=text,
                bytes=len(encoded),
                truncated=True,
            )

        return TaskOutputSnapshot(
            task_id=task_id,
            text=text,
            bytes=len(encoded),
            truncated=False,
        )

    async def clear(self, task_id: str) -> None:
        async with self._lock:
            self._chunks.pop(task_id, None)

    async def size_bytes(self, task_id: str) -> int:
        async with self._lock:
            text = "".join(self._chunks.get(task_id, []))
        return len(text.encode("utf-8"))
