from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agent import AgentTool, AgentToolResult, MemorySessionRepo
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
)
from resources import MemoryResourceStore, ResourceResolver
from runtime import AgentFactory, ToolRegistry
from tasks import (
    AgentChildSessionRequest,
    AgentTaskResult,
    AgentTaskRunner,
    MemoryTaskRuntime,
)


def _now() -> int:
    return int(time.time() * 1000)


def _model() -> Model:
    return make_litellm_model(provider="openai", model_id="gpt-test")


def _assistant(text: str, stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="litellm",
        provider="openai",
        model="gpt-test",
        stop_reason=stop_reason,
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
        stream.push(AssistantMessageEventDone(reason=stop_reason_to_done(message), message=message))

    stream.set_producer(produce())
    return stream


def stop_reason_to_done(message: AssistantMessage) -> str:
    return "toolUse" if message.stop_reason == "toolUse" else "stop"


def _agent_resource(
    name: str = "reviewer",
    *,
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
) -> dict[str, Any]:
    content: dict[str, Any] = {
        "name": name,
        "description": "Review changes",
        "systemPrompt": "Review carefully.",
    }
    if tools_allow is not None:
        content["toolsAllow"] = tools_allow
    if tools_deny is not None:
        content["toolsDeny"] = tools_deny
    return {
        "kind": "agent_definition",
        "name": name,
        "content": content,
    }


def _tool(name: str) -> AgentTool:
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


def _factory(
    store: MemoryResourceStore,
    *,
    tool_registry: ToolRegistry | None = None,
    stream_fn=None,
) -> AgentFactory:
    return AgentFactory(
        resource_resolver=ResourceResolver([store]),
        tool_registry=tool_registry or ToolRegistry(),
        default_model=_model(),
        stream_fn=stream_fn,
    )


@pytest.mark.asyncio
async def test_agent_task_runner_success_uses_child_session_and_records_result() -> None:
    store = MemoryResourceStore([_agent_resource()])
    session_repo = MemorySessionRepo()
    sessions = {}
    requests: dict[str, AgentChildSessionRequest] = {}

    async def session_factory(request: AgentChildSessionRequest):
        session = await session_repo.create(
            id=f"child-{request.task_id}",
            principal_id=request.principal_id,
        )
        sessions[request.task_id] = session
        requests[request.task_id] = request
        return session

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        assert context.messages[-1].role == "user"
        assert context.messages[-1].content == "Review this"
        return _stream_for(_assistant("Looks good."))

    runner = AgentTaskRunner(
        agent_factory=_factory(store, stream_fn=stream_fn),
        session_factory=session_factory,
    )
    runtime = MemoryTaskRuntime()
    parent_metadata = {
        "agentDepth": 0,
        "principalId": "principal-1",
        "parentSessionId": "parent-session-1",
        "parentToolCallId": "tool-1",
        "description": "Review change",
        "mode": "sync",
        "provenance": {"source": "test"},
    }

    spawned = await runtime.spawn(
        kind="agent",
        description="Run reviewer",
        metadata=parent_metadata,
        run=lambda context: runner.run(
            task_context=context,
            agent_name="reviewer",
            prompt="Review this",
            parent_metadata=parent_metadata,
        ),
    )
    completed = await runtime.wait(spawned.id)
    output = await runtime.read_output(spawned.id)

    assert completed.status == "completed"
    assert isinstance(completed.result, AgentTaskResult)
    assert completed.result.agent_name == "reviewer"
    assert completed.result.final_text == "Looks good."
    assert completed.result.status == "completed"
    assert completed.result.turns == 1
    assert completed.result.session_id == f"child-{spawned.id}"
    assert completed.result_metadata["agentName"] == "reviewer"
    assert completed.result_metadata["agentDepth"] == 1
    assert completed.result_metadata["sessionId"] == f"child-{spawned.id}"
    assert completed.result_metadata["stopReason"] == "stop"
    assert "Started agent reviewer." in output.text
    assert "Completed agent reviewer." in output.text
    assert "Looks good." in output.text

    request = requests[spawned.id]
    assert request.task_id == spawned.id
    assert request.agent_name == "reviewer"
    assert request.agent_depth == 1
    assert request.principal_id == "principal-1"
    assert request.parent_session_id == "parent-session-1"
    assert request.parent_tool_call_id == "tool-1"
    assert request.description == "Review change"
    assert request.mode == "sync"
    assert request.provenance == {"source": "test"}

    entries = await sessions[spawned.id].get_entries()
    messages = [entry.message for entry in entries if entry.type == "message"]
    assert [message.role for message in messages if message is not None] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_agent_task_runner_applies_agent_factory_tool_selection() -> None:
    store = MemoryResourceStore(
        [
            _agent_resource(
                tools_allow=["read_file", "write_file"],
                tools_deny=["write_file"],
            )
        ]
    )
    seen_tools: list[list[str]] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        seen_tools.append([tool.name for tool in context.tools or []])
        return _stream_for(_assistant("Read-only review done."))

    runner = AgentTaskRunner(
        agent_factory=_factory(
            store,
            tool_registry=ToolRegistry([_tool("read_file"), _tool("write_file")]),
            stream_fn=stream_fn,
        ),
        session_factory=lambda request: MemorySessionRepo().create(id=f"child-{request.task_id}"),
    )
    runtime = MemoryTaskRuntime()

    spawned = await runtime.spawn(
        kind="agent",
        description="Run reviewer",
        run=lambda context: runner.run(
            task_context=context,
            agent_name="reviewer",
            prompt="Review this",
        ),
    )
    completed = await runtime.wait(spawned.id)

    assert completed.status == "completed"
    assert seen_tools == [["read_file"]]


@pytest.mark.asyncio
async def test_agent_task_runner_factory_validation_failure_marks_task_failed() -> None:
    store = MemoryResourceStore([_agent_resource(tools_allow=["missing_tool"])])
    runner = AgentTaskRunner(
        agent_factory=_factory(store),
        session_factory=lambda request: MemorySessionRepo().create(id=f"child-{request.task_id}"),
    )
    runtime = MemoryTaskRuntime()

    spawned = await runtime.spawn(
        kind="agent",
        description="Run invalid reviewer",
        run=lambda context: runner.run(
            task_context=context,
            agent_name="reviewer",
            prompt="Review this",
        ),
    )
    failed = await runtime.wait(spawned.id)

    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.type == "AgentFactoryError"
    assert "Unknown tool" in failed.error.message


@pytest.mark.asyncio
async def test_agent_task_runner_stop_aborts_child_harness_and_cancels_task() -> None:
    store = MemoryResourceStore([_agent_resource()])
    stream_started = asyncio.Event()
    release_stream = asyncio.Event()

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, context, options
        stream = create_assistant_message_event_stream()

        async def produce() -> None:
            stream_started.set()
            await release_stream.wait()
            message = _assistant("late response")
            stream.push(AssistantMessageEventDone(reason="stop", message=message))

        stream.set_producer(produce())
        return stream

    runner = AgentTaskRunner(
        agent_factory=_factory(store, stream_fn=stream_fn),
        session_factory=lambda request: MemorySessionRepo().create(id=f"child-{request.task_id}"),
        abort_poll_seconds=0.01,
    )
    runtime = MemoryTaskRuntime()

    spawned = await runtime.spawn(
        kind="agent",
        description="Run slow reviewer",
        run=lambda context: runner.run(
            task_context=context,
            agent_name="reviewer",
            prompt="Review this",
        ),
    )
    await asyncio.wait_for(stream_started.wait(), timeout=1)

    cancelled = await runtime.stop(spawned.id, reason="user stopped it")
    release_stream.set()
    await asyncio.sleep(0)

    assert cancelled.status == "cancelled"
    assert cancelled.error is not None
    assert cancelled.error.message == "user stopped it"


@pytest.mark.asyncio
async def test_agent_task_runner_recursion_guard_fails_before_creating_session() -> None:
    store = MemoryResourceStore([_agent_resource()])
    created_sessions: list[str] = []

    async def session_factory(request: AgentChildSessionRequest):
        created_sessions.append(request.task_id)
        return await MemorySessionRepo().create(id=f"child-{request.task_id}")

    runner = AgentTaskRunner(
        agent_factory=_factory(store),
        session_factory=session_factory,
        max_depth=2,
    )
    runtime = MemoryTaskRuntime()

    spawned = await runtime.spawn(
        kind="agent",
        description="Run recursive reviewer",
        run=lambda context: runner.run(
            task_context=context,
            agent_name="reviewer",
            prompt="Review this",
            parent_metadata={"agentDepth": 2},
        ),
    )
    failed = await runtime.wait(spawned.id)

    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.type == "AgentTaskRunnerError"
    assert "max depth" in failed.error.message
    assert created_sessions == []
