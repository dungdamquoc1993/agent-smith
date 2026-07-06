from __future__ import annotations

import asyncio

import pytest

from agent_smith.core.tasks import (
    MemoryTaskOutputStore,
    MemoryTaskRuntime,
    TaskAlreadyFinishedError,
    TaskContext,
    TaskTimeoutError,
    UnknownTaskError,
)


@pytest.mark.asyncio
async def test_task_runtime_success_lifecycle_records_result_and_output() -> None:
    runtime = MemoryTaskRuntime()

    async def run(context: TaskContext) -> dict[str, object]:
        await context.append_output("hello ")
        await context.set_result_metadata({"turns": 1})
        await context.append_output("world")
        return {"ok": True, "nested": {"value": 1}}

    spawned = await runtime.spawn(
        kind="agent",
        description="Run a test agent",
        run=run,
        metadata={"agentName": "tester"},
    )
    assert spawned.status == "running"
    assert spawned.output_path is None

    completed = await runtime.wait(spawned.id)
    output = await runtime.read_output(spawned.id)

    assert completed.status == "completed"
    assert completed.started_at is not None
    assert completed.ended_at is not None
    assert completed.result == {"ok": True, "nested": {"value": 1}}
    assert completed.result_metadata == {"turns": 1}
    assert completed.metadata == {"agentName": "tester"}
    assert completed.output_bytes == len("hello world".encode("utf-8"))
    assert output.text == "hello world"
    assert output.bytes == completed.output_bytes
    assert output.truncated is False


@pytest.mark.asyncio
async def test_task_runtime_failed_lifecycle_records_error() -> None:
    runtime = MemoryTaskRuntime()

    async def run(context: TaskContext) -> None:
        await context.append_output("before failure")
        raise ValueError("boom")

    spawned = await runtime.spawn(kind="agent", description="Failing task", run=run)
    failed = await runtime.wait(spawned.id)

    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.type == "ValueError"
    assert failed.error.message == "boom"
    assert failed.output_bytes == len("before failure".encode("utf-8"))


@pytest.mark.asyncio
async def test_task_runtime_stop_sets_abort_and_records_cancel_reason() -> None:
    runtime = MemoryTaskRuntime()
    started = asyncio.Event()
    observed_abort = asyncio.Event()

    async def run(context: TaskContext) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            if context.abort_signal.is_set():
                observed_abort.set()

    spawned = await runtime.spawn(kind="agent", description="Long running task", run=run)
    await started.wait()

    cancelled = await runtime.stop(spawned.id, reason="user stopped it")

    assert observed_abort.is_set()
    assert cancelled.status == "cancelled"
    assert cancelled.error is not None
    assert cancelled.error.type == "cancelled"
    assert cancelled.error.message == "user stopped it"
    assert cancelled.ended_at is not None


@pytest.mark.asyncio
async def test_task_runtime_wait_timeout_does_not_stop_task() -> None:
    runtime = MemoryTaskRuntime()

    async def run(context: TaskContext) -> str:
        _ = context
        await asyncio.sleep(1)
        return "done"

    spawned = await runtime.spawn(kind="agent", description="Slow task", run=run)

    with pytest.raises(TaskTimeoutError, match="Timed out waiting"):
        await runtime.wait(spawned.id, timeout_seconds=0.01)

    still_running = await runtime.get(spawned.id)
    assert still_running.status == "running"

    cancelled = await runtime.stop(spawned.id)
    assert cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_memory_task_output_store_reads_full_and_tail_bytes() -> None:
    store = MemoryTaskOutputStore()

    await store.append("task-1", "abc")
    await store.append("task-1", "def")

    full = await store.read("task-1")
    tail = await store.read("task-1", max_bytes=3)
    empty_tail = await store.read("task-1", max_bytes=0)

    assert full.text == "abcdef"
    assert full.bytes == 6
    assert full.truncated is False
    assert tail.text == "def"
    assert tail.bytes == 6
    assert tail.truncated is True
    assert empty_tail.text == ""
    assert empty_tail.bytes == 6
    assert empty_tail.truncated is True


@pytest.mark.asyncio
async def test_task_runtime_unknown_and_terminal_errors() -> None:
    runtime = MemoryTaskRuntime()

    with pytest.raises(UnknownTaskError):
        await runtime.get("missing")
    with pytest.raises(UnknownTaskError):
        await runtime.read_output("missing")
    with pytest.raises(UnknownTaskError):
        await runtime.wait("missing")

    async def run(context: TaskContext) -> str:
        _ = context
        return "done"

    spawned = await runtime.spawn(kind="agent", description="Quick task", run=run)
    completed = await runtime.wait(spawned.id)
    assert completed.status == "completed"

    with pytest.raises(TaskAlreadyFinishedError):
        await runtime.stop(spawned.id)


@pytest.mark.asyncio
async def test_task_runtime_returns_isolated_snapshots() -> None:
    runtime = MemoryTaskRuntime()

    async def run(context: TaskContext) -> dict[str, object]:
        await context.set_result_metadata({"nested": {"turns": 1}})
        return {"nested": {"value": 1}}

    spawned = await runtime.spawn(
        kind="agent",
        description="Snapshot task",
        run=run,
        metadata={"nested": {"x": 1}},
    )
    spawned.metadata["nested"]["x"] = 99

    completed = await runtime.wait(spawned.id)
    completed.result["nested"]["value"] = 99
    completed.result_metadata["nested"]["turns"] = 99

    listed = await runtime.list()
    listed[0].metadata["nested"]["x"] = 42

    fresh = await runtime.get(spawned.id)
    assert fresh.metadata == {"nested": {"x": 1}}
    assert fresh.result == {"nested": {"value": 1}}
    assert fresh.result_metadata == {"nested": {"turns": 1}}
