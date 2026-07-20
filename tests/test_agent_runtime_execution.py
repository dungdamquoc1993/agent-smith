from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest

from agent_smith.core.agent import AgentHarnessStreamOptions, AgentTool, AgentToolResult
from agent_smith.core.agent.harness.compaction import (
    CompactionSettings,
    SUMMARIZATION_SYSTEM_PROMPT,
)
from agent_smith.core.agent.harness.session.session import Session
from agent_smith.core.llm.events import create_assistant_message_event_stream
from agent_smith.core.llm.models import make_litellm_model
from agent_smith.core.llm.types import (
    AssistantMessage,
    AssistantMessageEventDone,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    UserMessage,
    Usage,
    UsageCost,
)
from agent_smith.core.resources import ResourceResolver
from agent_smith.core.runtime import (
    AgentExecutionRequest,
    AgentRunStoreError,
    AgentRuntime,
    AgentRuntimeError,
    ToolRegistry,
)
from helpers.resource_stores import MemoryResourceStore
from helpers.run_stores import MemoryAgentRunStore
from helpers.sessions import MemorySessionRepo


def _now() -> int:
    return int(time.time() * 1_000)


def _model(*, context_window: int = 128_000) -> Model:
    return make_litellm_model(provider="openai", model_id="gpt-test").model_copy(
        update={"context_window": context_window}
    )


def _assistant(
    content: list[Any],
    *,
    stop_reason: str = "stop",
    input_tokens: int = 0,
    output_tokens: int = 0,
    response_id: str | None = None,
) -> AssistantMessage:
    cost = UsageCost(
        input=input_tokens / 1_000,
        output=output_tokens / 1_000,
        total=(input_tokens + output_tokens) / 1_000,
    )
    return AssistantMessage(
        content=content,
        api="litellm",
        provider="openai",
        model="gpt-test",
        response_id=response_id,
        usage=Usage(
            input=input_tokens,
            output=output_tokens,
            totalTokens=input_tokens + output_tokens,
            cost=cost,
        ),
        stop_reason=stop_reason,
        timestamp=_now(),
    )


def _stream(message: AssistantMessage):
    stream = create_assistant_message_event_stream()

    async def produce() -> None:
        stream.push(
            AssistantMessageEventDone(
                reason="toolUse" if message.stop_reason == "toolUse" else "stop",
                message=message,
            )
        )

    stream.set_producer(produce())
    return stream


def _resource() -> dict[str, Any]:
    return {
        "kind": "agent_definition",
        "name": "assistant",
        "content": {
            "name": "assistant",
            "description": "Tracked assistant",
            "systemPrompt": "Be helpful.",
        },
    }


def _tool() -> AgentTool:
    return AgentTool(
        name="lookup",
        label="Lookup",
        description="Look something up",
        parameters={"type": "object", "properties": {}},
        execute=lambda *_args: AgentToolResult(content=[TextContent(text="tool result")]),
    )


def _runtime(
    *,
    run_store: MemoryAgentRunStore,
    stream_fn,
    model: Model | None = None,
    tools: list[AgentTool] | None = None,
    compaction_settings: CompactionSettings | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        resource_resolver=ResourceResolver([MemoryResourceStore([_resource()])]),
        tool_registry=ToolRegistry(tools or []),
        default_model=model or _model(),
        stream_fn=stream_fn,
        run_store=run_store,
        compaction_settings=compaction_settings,
    )


@pytest.mark.asyncio
async def test_execute_records_call_usage_timing_and_session_entry() -> None:
    store = MemoryAgentRunStore()
    session = await MemorySessionRepo().create(
        id=str(uuid.uuid4()),
        principal_id=str(uuid.uuid4()),
    )
    provider_calls = 0

    def stream_fn(model, context, options=None):
        nonlocal provider_calls
        provider_calls += 1
        assert options.headers == {"Authorization": "secret"}
        return _stream(
            _assistant(
                [TextContent(text="done")],
                input_tokens=10,
                output_tokens=4,
                response_id="response-1",
            )
        )

    runtime = _runtime(run_store=store, stream_fn=stream_fn)
    runtime.stream_options = AgentHarnessStreamOptions(
        headers={"Authorization": "secret"},
        metadata={"apiKey": "must-not-persist"},
    )
    started_after_persist = False

    async def on_started(run_id: str) -> None:
        nonlocal started_after_persist
        started_after_persist = run_id in store.runs

    result = await runtime.execute(
        AgentExecutionRequest(
            session=session,
            agent_name="assistant",
            prompt="hello",
            flow="test",
            prompt_options=None,
            on_started=on_started,
        )
    )

    assert started_after_persist is True
    assert provider_calls == 1
    assert result.call_count == 1
    assert result.usage.input == 10
    assert result.usage.output == 4
    assert result.recording_status == "complete"
    assert "llmCallId" not in result.message.model_dump(mode="json", by_alias=True)
    run = store.runs[result.run_id]
    call = next(iter(store.calls.values()))
    assert run["status"] == "completed"
    assert run["recording_status"] == "complete"
    assert call["status"] == "succeeded"
    assert call["provider_response_id"] == "response-1"
    assert call["session_entry_id"] is not None
    assert call["metadata"] == {"messageCount": 1, "toolCount": 0}
    assert "Authorization" not in str(call["metadata"])


@pytest.mark.asyncio
async def test_execute_aggregates_multiple_tool_loop_calls() -> None:
    store = MemoryAgentRunStore()
    session = await MemorySessionRepo().create(principal_id="principal-1")
    responses = iter(
        [
            _assistant(
                [ToolCall(id="call-1", name="lookup", arguments={})],
                stop_reason="toolUse",
                input_tokens=7,
                output_tokens=2,
            ),
            _assistant(
                [TextContent(text="final")],
                input_tokens=11,
                output_tokens=5,
            ),
        ]
    )
    runtime = _runtime(
        run_store=store,
        stream_fn=lambda *_args, **_kwargs: _stream(next(responses)),
        tools=[_tool()],
    )

    result = await runtime.execute(
        AgentExecutionRequest(
            session=session,
            agent_name="assistant",
            prompt="use the tool",
            flow="test",
        )
    )

    assert result.call_count == 2
    assert result.usage.input == 18
    assert result.usage.output == 7
    assert result.usage.total_tokens == 25
    assert [call["sequence"] for call in store.calls.values()] == [1, 2]
    assert all(call["status"] == "succeeded" for call in store.calls.values())


@pytest.mark.asyncio
async def test_execute_tracks_compaction_as_a_separate_call() -> None:
    store = MemoryAgentRunStore()
    session = await MemorySessionRepo().create(principal_id="principal-1")
    await session.append_message(UserMessage(content="old prompt " * 100, timestamp=_now()))
    await session.append_message(
        _assistant([TextContent(text="old answer " * 100)], input_tokens=20, output_tokens=10)
    )
    await session.append_message(UserMessage(content="recent prompt", timestamp=_now()))
    await session.append_message(_assistant([TextContent(text="recent answer")]))
    purposes_seen: list[str] = []

    def stream_fn(model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        if context.system_prompt == SUMMARIZATION_SYSTEM_PROMPT:
            return _stream(
                _assistant([TextContent(text="summary")], input_tokens=30, output_tokens=6)
            )
        return _stream(_assistant([TextContent(text="done")], input_tokens=8, output_tokens=3))

    runtime = _runtime(
        run_store=store,
        stream_fn=stream_fn,
        model=_model(context_window=50),
        compaction_settings=CompactionSettings(reserve_tokens=20, keep_recent_tokens=2),
    )
    result = await runtime.execute(
        AgentExecutionRequest(
            session=session,
            agent_name="assistant",
            prompt="new prompt",
            flow="test",
        )
    )
    purposes_seen = [call["purpose"] for call in store.calls.values()]

    assert purposes_seen == ["compaction", "agent_turn"]
    assert result.call_count == 2
    assert result.usage.total_tokens == 47
    compaction = next(call for call in store.calls.values() if call["purpose"] == "compaction")
    assert compaction["session_entry_id"] is None


class _StartRunFailureStore(MemoryAgentRunStore):
    async def start_run(self, run) -> None:
        raise AgentRunStoreError("database unavailable")


class _StartCallFailureStore(MemoryAgentRunStore):
    async def start_call(self, call) -> None:
        raise AgentRunStoreError("database unavailable")


class _FinishCallFailureStore(MemoryAgentRunStore):
    def __init__(self) -> None:
        super().__init__()
        self.finish_attempts = 0

    async def finish_call(self, finish) -> None:
        self.finish_attempts += 1
        raise AgentRunStoreError("database unavailable")


class _CountingStore(MemoryAgentRunStore):
    def __init__(self) -> None:
        super().__init__()
        self.operations: list[str] = []

    async def start_run(self, run) -> None:
        self.operations.append("start_run")
        await super().start_run(run)

    async def start_call(self, call) -> None:
        self.operations.append("start_call")
        await super().start_call(call)

    async def finish_call(self, finish) -> None:
        self.operations.append("finish_call")
        await super().finish_call(finish)

    async def link_call_session_entry(self, call_id, session_entry_id) -> None:
        self.operations.append("link_call_session_entry")
        await super().link_call_session_entry(call_id, session_entry_id)

    async def finish_run(self, finish) -> None:
        self.operations.append("finish_run")
        await super().finish_run(finish)


class _FailingAssistantSession(Session):
    async def append_message(self, message) -> str:
        if isinstance(message, AssistantMessage):
            raise OSError("session database unavailable")
        return await super().append_message(message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("store", "expected_stage"),
    [(_StartRunFailureStore(), "run_start"), (_StartCallFailureStore(), "llm_call_start")],
)
async def test_start_persistence_failure_never_calls_provider(store, expected_stage) -> None:
    session = await MemorySessionRepo().create(principal_id="principal-1")
    provider_calls = 0

    def stream_fn(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _stream(_assistant([TextContent(text="must not run")]))

    runtime = _runtime(run_store=store, stream_fn=stream_fn)
    with pytest.raises(AgentRuntimeError) as raised:
        await runtime.execute(
            AgentExecutionRequest(
                session=session,
                agent_name="assistant",
                prompt="hello",
                flow="test",
            )
        )

    assert raised.value.stage == expected_stage
    assert raised.value.retryable is True
    assert raised.value.recording_status == "degraded"
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_finalize_failure_retries_without_recalling_provider_and_degrades_recording() -> None:
    store = _FinishCallFailureStore()
    session = await MemorySessionRepo().create(principal_id="principal-1")
    provider_calls = 0

    def stream_fn(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _stream(_assistant([TextContent(text="done")], input_tokens=3, output_tokens=2))

    result = await _runtime(run_store=store, stream_fn=stream_fn).execute(
        AgentExecutionRequest(
            session=session,
            agent_name="assistant",
            prompt="hello",
            flow="test",
        )
    )

    assert provider_calls == 1
    assert store.finish_attempts == 2
    assert len(store.calls) == 1
    assert result.recording_status == "degraded"
    assert store.runs[result.run_id]["status"] == "completed"
    assert store.runs[result.run_id]["recording_status"] == "degraded"


@pytest.mark.asyncio
async def test_normal_call_uses_two_call_writes_and_finalizes_with_session_link() -> None:
    store = _CountingStore()
    session = await MemorySessionRepo().create(principal_id="principal-1")

    result = await _runtime(
        run_store=store,
        stream_fn=lambda *_args, **_kwargs: _stream(_assistant([TextContent(text="done")])),
    ).execute(
        AgentExecutionRequest(
            session=session,
            agent_name="assistant",
            prompt="hello",
            flow="test",
        )
    )

    assert store.operations == ["start_run", "start_call", "finish_call", "finish_run"]
    assert next(iter(store.calls.values()))["session_entry_id"] is not None
    assert result.recording_status == "complete"


@pytest.mark.asyncio
async def test_session_persistence_failure_finalizes_call_without_link_and_degrades_run() -> None:
    store = MemoryAgentRunStore()
    base_session = await MemorySessionRepo().create(principal_id="principal-1")
    session = _FailingAssistantSession(base_session.get_storage())
    provider_calls = 0

    def stream_fn(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _stream(_assistant([TextContent(text="done")], input_tokens=4, output_tokens=2))

    with pytest.raises(AgentRuntimeError) as raised:
        await _runtime(run_store=store, stream_fn=stream_fn).execute(
            AgentExecutionRequest(
                session=session,
                agent_name="assistant",
                prompt="hello",
                flow="test",
            )
        )

    error = raised.value
    assert provider_calls == 1
    assert error.stage == "session_persistence"
    assert error.recording_status == "degraded"
    call = next(iter(store.calls.values()))
    assert call["status"] == "succeeded"
    assert call["session_entry_id"] is None
    assert store.runs[error.run_id]["status"] == "failed"
    assert store.runs[error.run_id]["recording_status"] == "degraded"


@pytest.mark.asyncio
async def test_provider_failure_returns_safe_error_and_partial_aggregate() -> None:
    store = MemoryAgentRunStore()
    session = await MemorySessionRepo().create(principal_id="principal-1")
    failed_message = _assistant(
        [TextContent(text="")],
        stop_reason="error",
        input_tokens=5,
        output_tokens=1,
    ).model_copy(update={"error_message": "secret provider payload"})

    with pytest.raises(AgentRuntimeError) as raised:
        await _runtime(
            run_store=store,
            stream_fn=lambda *_args, **_kwargs: _stream(failed_message),
        ).execute(
            AgentExecutionRequest(
                session=session,
                agent_name="assistant",
                prompt="hello",
                flow="test",
            )
        )

    error = raised.value
    assert error.stage == "provider"
    assert error.public_message == "The model provider request failed."
    assert "secret provider payload" not in error.public_message
    assert error.usage.total_tokens == 6
    assert error.call_count == 1
    assert error.recording_status == "complete"
    call = next(iter(store.calls.values()))
    assert call["status"] == "failed"
    assert call["error_message"] == "LLM call failed"
    assert store.runs[error.run_id]["status"] == "failed"


@pytest.mark.asyncio
async def test_cancellation_aborts_the_active_call_and_run() -> None:
    store = MemoryAgentRunStore()
    session = await MemorySessionRepo().create(principal_id="principal-1")
    provider_started = asyncio.Event()

    def stream_fn(*_args, **_kwargs):
        stream = create_assistant_message_event_stream()

        async def produce() -> None:
            provider_started.set()
            await asyncio.Event().wait()

        stream.set_producer(produce())
        return stream

    task = asyncio.create_task(
        _runtime(run_store=store, stream_fn=stream_fn).execute(
            AgentExecutionRequest(
                session=session,
                agent_name="assistant",
                prompt="wait",
                flow="test",
            )
        )
    )
    await asyncio.wait_for(provider_started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert next(iter(store.calls.values()))["status"] == "aborted"
    assert next(iter(store.runs.values()))["status"] == "aborted"


@pytest.mark.asyncio
async def test_model_change_between_tool_turns_is_recorded_per_call() -> None:
    store = MemoryAgentRunStore()
    session = await MemorySessionRepo().create(principal_id="principal-1")
    second_model = make_litellm_model(provider="openai", model_id="gpt-second")
    harness_holder: dict[str, Any] = {}
    provider_models: list[str] = []
    switch_count = 0

    async def switch_model_on_tool_start(event) -> None:
        nonlocal switch_count
        if event.type == "tool_execution_start":
            switch_count += 1
            await harness_holder["harness"].set_model(second_model)

    tool = AgentTool(
        name="switch_model",
        label="Switch model",
        description="Switch model",
        parameters={"type": "object", "properties": {}},
        execute=lambda *_args: AgentToolResult(content=[TextContent(text="switched")]),
    )
    responses = iter(
        [
            _assistant(
                [ToolCall(id="switch-1", name="switch_model", arguments={})],
                stop_reason="toolUse",
            ),
            AssistantMessage(
                content=[TextContent(text="new model response")],
                api="litellm",
                provider="openai",
                model="gpt-second",
                timestamp=_now(),
            ),
        ]
    )
    def stream_fn(model, *_args, **_kwargs):
        provider_models.append(model.id)
        return _stream(next(responses))

    runtime = _runtime(
        run_store=store,
        stream_fn=stream_fn,
        tools=[tool],
    )
    await runtime.execute(
        AgentExecutionRequest(
            session=session,
            agent_name="assistant",
            prompt="switch",
            flow="test",
            harness_setup=lambda harness: harness_holder.update(harness=harness),
            event_sink=switch_model_on_tool_start,
        )
    )

    assert switch_count == 1
    assert provider_models == ["gpt-test", "gpt-second"]
    assert [call["requested_model"] for call in store.calls.values()] == [
        "gpt-test",
        "gpt-second",
    ]
