from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agent_smith.core.agent import AgentTool, AgentToolResult, MemorySessionRepo
from agent_smith.core.agent.validation import validate_tool_arguments
from agent_smith.core.llm.events import create_assistant_message_event_stream
from agent_smith.core.llm.models import make_litellm_model
from agent_smith.core.llm.types import (
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
from agent_smith.core.resources import MemoryResourceStore, ResourceResolver
from agent_smith.core.runtime import AgentFactory, ToolRegistry
from agent_smith.core.tasks import (
    AgentChildSessionRequest,
    AgentTaskRunner,
    MemoryTaskRuntime,
    TaskContext,
    UnknownTaskError,
)
from agent_smith.core.tools import (
    TASK_TOOL_NAME,
    TASK_OUTPUT_TOOL_NAME,
    TASK_STOP_TOOL_NAME,
    create_task_tool,
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


def _child_session_options(request: AgentChildSessionRequest) -> dict[str, Any]:
    return {
        "id": f"child-{request.task_id}",
        "principal_id": request.principal_id,
        "kind": "agent_run",
        "parent_session_id": request.parent_session_id,
        "agent_name": request.agent_name,
        "origin_task_id": request.task_id,
        "provenance": {
            **request.provenance,
            "trigger": "task_tool",
            "mode": request.mode or "sync",
        },
    }


def _runner(runtime_store: MemoryResourceStore, *, stream_fn=None) -> AgentTaskRunner:
    return AgentTaskRunner(
        agent_factory=_factory(runtime_store, stream_fn=stream_fn),
        session_factory=lambda request: MemorySessionRepo().create(**_child_session_options(request)),
        abort_poll_seconds=0.01,
    )


@pytest.mark.asyncio
async def test_agent_tool_sync_runs_agent_run_to_final_response() -> None:
    store = MemoryResourceStore([_agent_resource()])
    runtime = MemoryTaskRuntime()

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        assert context.messages[-1].role == "user"
        assert context.messages[-1].content == "Review this"
        return _stream_for(_assistant("Looks good."))

    tool = create_task_tool(runtime, _runner(store, stream_fn=stream_fn))

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
    assert result.details["task"]["metadata"]["parentToolCallId"] == "agent-1"
    assert result.details["task"]["metadata"]["description"] == "Review change"


@pytest.mark.asyncio
async def test_agent_tool_merges_parent_metadata_into_task_and_session_request() -> None:
    store = MemoryResourceStore([_agent_resource()])
    runtime = MemoryTaskRuntime()
    captured_requests: list[AgentChildSessionRequest] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, context, options
        return _stream_for(_assistant("Done."))

    async def session_factory(request: AgentChildSessionRequest):
        captured_requests.append(request)
        return await MemorySessionRepo().create(**_child_session_options(request))

    runner = AgentTaskRunner(
        agent_factory=_factory(store, stream_fn=stream_fn),
        session_factory=session_factory,
        abort_poll_seconds=0.01,
    )
    tool = create_task_tool(
        runtime,
        runner,
        parent_metadata=lambda: {
            "agentDepth": 1,
            "principalId": "principal-1",
            "parentSessionId": "chat-session-1",
            "provenance": {"scope": "chat"},
        },
    )

    result = await tool.execute(
        "agent-call-1",
        {
            "agent_name": "reviewer",
            "description": "Review change",
            "prompt": "Review this",
            "mode": "sync",
        },
        None,
        None,
    )

    metadata = result.details["task"]["metadata"]
    assert metadata["agentDepth"] == 1
    assert metadata["parentToolCallId"] == "agent-call-1"
    assert metadata["principalId"] == "principal-1"
    assert metadata["parentSessionId"] == "chat-session-1"
    assert metadata["provenance"] == {
        "scope": "chat",
        "trigger": "task_tool",
        "mode": "sync",
    }

    request = captured_requests[0]
    assert request.agent_depth == 2
    assert request.principal_id == "principal-1"
    assert request.parent_session_id == "chat-session-1"
    assert request.parent_tool_call_id == "agent-call-1"
    assert request.description == "Review change"
    assert request.mode == "sync"
    assert request.provenance == {
        "scope": "chat",
        "trigger": "task_tool",
        "mode": "sync",
    }


@pytest.mark.asyncio
async def test_agent_tool_async_launch_then_task_output_blocks_to_result() -> None:
    store = MemoryResourceStore([_agent_resource()])
    runtime = MemoryTaskRuntime()
    agent_tool = create_task_tool(
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

    agent_tool = create_task_tool(runtime, _runner(store, stream_fn=stream_fn))
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
    agent_tool = create_task_tool(runtime, _runner(store))
    output_tool = create_task_output_tool(runtime)
    stop_tool = create_task_stop_tool(runtime)

    validate_tool_arguments(
        agent_tool,
        ToolCall(
            id="agent-1",
            name="task",
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
                name="task",
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
                name="task",
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

    assert TASK_TOOL_NAME not in base.names()
    assert TASK_OUTPUT_TOOL_NAME not in base.names()
    assert TASK_STOP_TOOL_NAME not in base.names()
    assert TASK_TOOL_NAME not in runtime_only.names()
    assert TASK_OUTPUT_TOOL_NAME in runtime_only.names()
    assert TASK_STOP_TOOL_NAME in runtime_only.names()
    assert TASK_TOOL_NAME in with_agent.names()
    assert TASK_OUTPUT_TOOL_NAME in with_agent.names()
    assert TASK_STOP_TOOL_NAME in with_agent.names()
