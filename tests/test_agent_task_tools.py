from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agent import AgentTool, AgentToolResult, MemorySessionRepo
from agent.validation import validate_tool_arguments
from ai.events import create_assistant_message_event_stream
from ai.models import make_litellm_model
from ai.types import (
    AssistantMessage,
    AssistantMessageEventDone,
    AssistantMessageEventStart,
    AssistantMessageEventTextDelta,
    AssistantMessageEventTextEnd,
    AssistantMessageEventTextStart,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
)
from resources import MemoryResourceStore, ResourceResolver
from runtime import AgentFactory, ToolRegistry
from tasks import AgentTaskRunner, MemoryTaskRuntime, TaskContext, UnknownTaskError
from tools import (
    AGENT_TOOL_NAME,
    TASK_OUTPUT_TOOL_NAME,
    TASK_STOP_TOOL_NAME,
    create_agent_tool,
    create_base_tool_registry,
    create_task_output_tool,
    create_task_stop_tool,
)


def _now() -> int:
    return int(time.time() * 1000)


def _model() -> Model:
    return make_litellm_model(provider="openai", model_id="gpt-test")


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="litellm",
        provider="openai",
        model="gpt-test",
        stop_reason="stop",
        timestamp=_now(),
    )


def _stream_for(message: AssistantMessage):
    stream = create_assistant_message_event_stream()

    async def produce() -> None:
        partial = message.model_copy(update={"content": []}, deep=True)
        stream.push(AssistantMessageEventStart(partial=partial))
        partial = message.model_copy(update={"content": message.content}, deep=True)
        stream.push(AssistantMessageEventTextStart(content_index=0, partial=partial))
        stream.push(
            AssistantMessageEventTextDelta(
                content_index=0,
                delta=message.content[0].text,
                partial=partial,
            )
        )
        stream.push(
            AssistantMessageEventTextEnd(
                content_index=0,
                content=message.content[0].text,
                partial=partial,
            )
        )
        stream.push(AssistantMessageEventDone(reason="stop", message=message))

    stream.set_producer(produce())
    return stream


def _agent_resource(name: str = "reviewer") -> dict[str, Any]:
    return {
        "kind": "agent_definition",
        "name": name,
        "content": {
            "name": name,
            "description": "Review changes",
            "systemPrompt": "Review carefully.",
        },
    }


def _noop_tool(name: str) -> AgentTool:
    def execute(tool_call_id, args, signal, update):
        _ = tool_call_id, args, signal, update
        return AgentToolResult(content=[TextContent(text="ok")])

    return AgentTool(
        name=name,
        label=name,
        description=f"{name} tool",
        parameters={"type": "object", "properties": {}},
        execute=execute,
    )


def _factory(store: MemoryResourceStore, *, stream_fn=None) -> AgentFactory:
    return AgentFactory(
        resource_resolver=ResourceResolver([store]),
        tool_registry=ToolRegistry([_noop_tool("read_file")]),
        default_model=_model(),
        stream_fn=stream_fn,
    )


def _runner(runtime_store: MemoryResourceStore, *, stream_fn=None) -> AgentTaskRunner:
    return AgentTaskRunner(
        agent_factory=_factory(runtime_store, stream_fn=stream_fn),
        session_factory=lambda task_id: MemorySessionRepo().create(id=f"child-{task_id}"),
        abort_poll_seconds=0.01,
    )


@pytest.mark.asyncio
async def test_agent_tool_sync_runs_sub_agent_to_final_response() -> None:
    store = MemoryResourceStore([_agent_resource()])
    runtime = MemoryTaskRuntime()

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        assert context.messages[-1].role == "user"
        assert context.messages[-1].content == "Review this"
        return _stream_for(_assistant("Looks good."))

    tool = create_agent_tool(runtime, _runner(store, stream_fn=stream_fn))

    result = await tool.execute(
        "agent-1",
        {
            "agent_name": "reviewer",
            "description": "Review change",
            "prompt": "Review this",
            "mode": "sync",
        },
        None,
        None,
    )

    assert result.content[0].text == "Looks good."
    assert result.details["status"] == "completed"
    assert result.details["agentName"] == "reviewer"
    assert result.details["result"]["finalText"] == "Looks good."
    assert "Started agent reviewer." in result.details["output"]["text"]
    assert "Completed agent reviewer." in result.details["output"]["text"]
    assert result.details["task"]["status"] == "completed"


@pytest.mark.asyncio
async def test_agent_tool_async_launch_then_task_output_blocks_to_result() -> None:
    store = MemoryResourceStore([_agent_resource()])
    runtime = MemoryTaskRuntime()
    agent_tool = create_agent_tool(
        runtime,
        _runner(store, stream_fn=lambda model, context, options=None: _stream_for(_assistant("Done."))),
    )
    output_tool = create_task_output_tool(runtime)

    launched = await agent_tool.execute(
        "agent-1",
        {
            "agent_name": "reviewer",
            "description": "Review change",
            "prompt": "Review this",
            "mode": "async",
        },
        None,
        None,
    )
    assert launched.details["status"] == "launched"
    task_id = launched.details["taskId"]

    output = await output_tool.execute(
        "task-output-1",
        {"task_id": task_id, "block": True, "timeout_seconds": 1},
        None,
        None,
    )

    assert output.details["retrievalStatus"] == "success"
    assert output.details["task"]["status"] == "completed"
    assert output.details["task"]["result"]["finalText"] == "Done."
    assert "Done." in output.details["output"]["text"]


@pytest.mark.asyncio
async def test_task_output_nonblocking_running_task_returns_not_ready() -> None:
    runtime = MemoryTaskRuntime()
    output_tool = create_task_output_tool(runtime)
    started = asyncio.Event()

    async def run(context: TaskContext) -> str:
        await context.append_output("working")
        started.set()
        await asyncio.sleep(1)
        return "done"

    spawned = await runtime.spawn(kind="agent", description="Slow task", run=run)
    await asyncio.wait_for(started.wait(), timeout=1)
    result = await output_tool.execute(
        "task-output-1",
        {"task_id": spawned.id, "block": False},
        None,
        None,
    )
    await runtime.stop(spawned.id)

    assert result.details["retrievalStatus"] == "not_ready"
    assert result.details["task"]["status"] == "running"
    assert result.details["output"]["text"] == "working"


@pytest.mark.asyncio
async def test_task_output_block_timeout_does_not_stop_task() -> None:
    runtime = MemoryTaskRuntime()
    output_tool = create_task_output_tool(runtime)

    async def run(context: TaskContext) -> str:
        _ = context
        await asyncio.sleep(1)
        return "done"

    spawned = await runtime.spawn(kind="agent", description="Slow task", run=run)
    result = await output_tool.execute(
        "task-output-1",
        {"task_id": spawned.id, "block": True, "timeout_seconds": 0.01},
        None,
        None,
    )
    running = await runtime.get(spawned.id)
    await runtime.stop(spawned.id)

    assert result.details["retrievalStatus"] == "timeout"
    assert result.details["task"]["status"] == "running"
    assert running.status == "running"


@pytest.mark.asyncio
async def test_task_stop_cancels_async_agent_task_and_output_reports_cancelled() -> None:
    store = MemoryResourceStore([_agent_resource()])
    runtime = MemoryTaskRuntime()
    stream_started = asyncio.Event()
    release_stream = asyncio.Event()

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, context, options
        stream = create_assistant_message_event_stream()

        async def produce() -> None:
            stream_started.set()
            await release_stream.wait()
            stream.push(AssistantMessageEventDone(reason="stop", message=_assistant("late")))

        stream.set_producer(produce())
        return stream

    agent_tool = create_agent_tool(runtime, _runner(store, stream_fn=stream_fn))
    output_tool = create_task_output_tool(runtime)
    stop_tool = create_task_stop_tool(runtime)

    launched = await agent_tool.execute(
        "agent-1",
        {
            "agent_name": "reviewer",
            "description": "Review change",
            "prompt": "Review this",
            "mode": "async",
        },
        None,
        None,
    )
    task_id = launched.details["taskId"]
    await asyncio.wait_for(stream_started.wait(), timeout=1)

    stopped = await stop_tool.execute(
        "task-stop-1",
        {"task_id": task_id, "reason": "No longer needed"},
        None,
        None,
    )
    release_stream.set()
    output = await output_tool.execute(
        "task-output-1",
        {"task_id": task_id, "block": True},
        None,
        None,
    )

    assert stopped.details["stopped"] is True
    assert stopped.details["status"] == "cancelled"
    assert output.details["task"]["status"] == "cancelled"
    assert output.details["task"]["error"]["message"] == "No longer needed"


@pytest.mark.asyncio
async def test_task_stop_completed_task_returns_snapshot_without_stopping() -> None:
    runtime = MemoryTaskRuntime()
    stop_tool = create_task_stop_tool(runtime)
    spawned = await runtime.spawn(
        kind="agent",
        description="Quick task",
        run=lambda context: _return_result(context, "done"),
    )
    await runtime.wait(spawned.id)

    result = await stop_tool.execute(
        "task-stop-1",
        {"task_id": spawned.id},
        None,
        None,
    )

    assert result.details["stopped"] is False
    assert result.details["status"] == "completed"


async def _return_result(context: TaskContext, value: str) -> str:
    _ = context
    return value


@pytest.mark.asyncio
async def test_task_tools_raise_clear_error_for_unknown_task() -> None:
    runtime = MemoryTaskRuntime()
    output_tool = create_task_output_tool(runtime)
    stop_tool = create_task_stop_tool(runtime)

    with pytest.raises(UnknownTaskError, match="Unknown task: missing"):
        await output_tool.execute("task-output-1", {"task_id": "missing"}, None, None)
    with pytest.raises(UnknownTaskError, match="Unknown task: missing"):
        await stop_tool.execute("task-stop-1", {"task_id": "missing"}, None, None)


def test_agent_task_tool_schemas_validate_required_fields_and_modes() -> None:
    runtime = MemoryTaskRuntime()
    store = MemoryResourceStore([_agent_resource()])
    agent_tool = create_agent_tool(runtime, _runner(store))
    output_tool = create_task_output_tool(runtime)
    stop_tool = create_task_stop_tool(runtime)

    validate_tool_arguments(
        agent_tool,
        ToolCall(
            id="agent-1",
            name="agent",
            arguments={
                "agent_name": "reviewer",
                "description": "Review",
                "prompt": "Review this",
                "mode": "sync",
            },
        ),
    )
    with pytest.raises(ValueError, match="mode"):
        validate_tool_arguments(
            agent_tool,
            ToolCall(
                id="agent-2",
                name="agent",
                arguments={
                    "agent_name": "reviewer",
                    "description": "Review",
                    "prompt": "Review this",
                    "mode": "foreground",
                },
            ),
        )
    with pytest.raises(ValueError, match="agent_name"):
        validate_tool_arguments(
            agent_tool,
            ToolCall(
                id="agent-3",
                name="agent",
                arguments={"description": "Review", "prompt": "Review this"},
            ),
        )
    validate_tool_arguments(
        output_tool,
        ToolCall(id="output-1", name="task_output", arguments={"task_id": "task-1"}),
    )
    validate_tool_arguments(
        stop_tool,
        ToolCall(id="stop-1", name="task_stop", arguments={"task_id": "task-1"}),
    )


def test_base_registry_optionally_adds_task_tools() -> None:
    runtime = MemoryTaskRuntime()
    store = MemoryResourceStore([_agent_resource()])
    runner = _runner(store)

    base = create_base_tool_registry()
    runtime_only = create_base_tool_registry(task_runtime=runtime)
    with_agent = create_base_tool_registry(task_runtime=runtime, agent_runner=runner)

    assert AGENT_TOOL_NAME not in base.names()
    assert TASK_OUTPUT_TOOL_NAME not in base.names()
    assert TASK_STOP_TOOL_NAME not in base.names()
    assert AGENT_TOOL_NAME not in runtime_only.names()
    assert TASK_OUTPUT_TOOL_NAME in runtime_only.names()
    assert TASK_STOP_TOOL_NAME in runtime_only.names()
    assert AGENT_TOOL_NAME in with_agent.names()
    assert TASK_OUTPUT_TOOL_NAME in with_agent.names()
    assert TASK_STOP_TOOL_NAME in with_agent.names()
