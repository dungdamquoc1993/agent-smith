from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agent_smith.agent import (
    AfterToolCallContext,
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    agent_loop,
    agent_loop_continue,
)
from agent_smith.ai.events import create_assistant_message_event_stream
from agent_smith.ai.models import make_litellm_model
from agent_smith.ai.types import (
    AssistantMessage,
    AssistantMessageEventDone,
    AssistantMessageEventStart,
    AssistantMessageEventTextDelta,
    AssistantMessageEventTextEnd,
    AssistantMessageEventTextStart,
    AssistantMessageEventToolcallEnd,
    AssistantMessageEventToolcallStart,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    UserMessage,
)


def _now() -> int:
    return int(time.time() * 1000)


def _model() -> Model:
    return make_litellm_model(provider="openai", model_id="gpt-test")


def _user(text: str = "hello") -> UserMessage:
    return UserMessage(content=text, timestamp=_now())


def _assistant(content: list[Any], stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=content,
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
        for index, block in enumerate(message.content):
            partial = message.model_copy(update={"content": message.content[: index + 1]}, deep=True)
            if isinstance(block, TextContent):
                stream.push(AssistantMessageEventTextStart(content_index=index, partial=partial))
                stream.push(
                    AssistantMessageEventTextDelta(
                        content_index=index,
                        delta=block.text,
                        partial=partial,
                    )
                )
                stream.push(
                    AssistantMessageEventTextEnd(
                        content_index=index,
                        content=block.text,
                        partial=partial,
                    )
                )
            elif isinstance(block, ToolCall):
                stream.push(AssistantMessageEventToolcallStart(content_index=index, partial=partial))
                stream.push(
                    AssistantMessageEventToolcallEnd(
                        content_index=index,
                        tool_call=block,
                        partial=partial,
                    )
                )
        stream.push(
            AssistantMessageEventDone(
                reason="toolUse" if message.stop_reason == "toolUse" else "stop",
                message=message,
            )
        )

    stream.set_producer(produce())
    return stream


@pytest.mark.asyncio
async def test_agent_loop_event_lifecycle_and_result() -> None:
    message = _assistant([TextContent(text="hi")])

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        assert model.id == "gpt-test"
        assert context.messages[0].role == "user"
        assert options is not None
        return _stream_for(message)

    stream = agent_loop(
        [_user()],
        AgentContext(messages=[]),
        AgentLoopConfig(model=_model(), temperature=0.2),
        stream_fn=stream_fn,
    )

    events = [event async for event in stream]
    result = await stream.result()

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert result == events[-1].messages
    assert [message.role for message in result] == ["user", "assistant"]


def test_agent_loop_continue_rejects_invalid_contexts() -> None:
    config = AgentLoopConfig(model=_model())

    with pytest.raises(ValueError, match="no messages"):
        agent_loop_continue(AgentContext(messages=[]), config)

    with pytest.raises(ValueError, match="assistant"):
        agent_loop_continue(AgentContext(messages=[_assistant([TextContent(text="done")])]), config)


@pytest.mark.asyncio
async def test_tool_call_executes_and_continues_until_text_response() -> None:
    calls = 0

    async def execute(tool_call_id, params, signal=None, on_update=None):
        assert tool_call_id == "call-1"
        assert params == {"x": 1}
        if on_update:
            on_update(AgentToolResult(content=[TextContent(text="working")], details={"step": 1}))
        return AgentToolResult(content=[TextContent(text="tool ok")], details={"ok": True})

    tool = AgentTool(
        name="do_it",
        label="Do it",
        description="Does it",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
        },
        execute=execute,
    )
    first = _assistant(
        [ToolCall(id="call-1", name="do_it", arguments={"x": 1})],
        stop_reason="toolUse",
    )
    second = _assistant([TextContent(text="finished")])

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        nonlocal calls
        calls += 1
        assert options is not None
        return _stream_for(first if calls == 1 else second)

    stream = agent_loop(
        [_user()],
        AgentContext(messages=[], tools=[tool]),
        AgentLoopConfig(model=_model()),
        stream_fn=stream_fn,
    )
    events = [event async for event in stream]
    result = await stream.result()

    assert calls == 2
    assert [message.role for message in result] == ["user", "assistant", "toolResult", "assistant"]
    assert result[2].tool_call_id == "call-1"
    assert result[2].content[0].text == "tool ok"
    assert "tool_execution_update" in [event.type for event in events]


@pytest.mark.asyncio
async def test_parallel_tools_emit_end_by_completion_but_messages_by_source_order() -> None:
    completion_order: list[str] = []

    async def execute_slow(tool_call_id, params, signal=None, on_update=None):
        await asyncio.sleep(0.02)
        completion_order.append(tool_call_id)
        return AgentToolResult(content=[TextContent(text=tool_call_id)], details={})

    async def execute_fast(tool_call_id, params, signal=None, on_update=None):
        await asyncio.sleep(0.001)
        completion_order.append(tool_call_id)
        return AgentToolResult(content=[TextContent(text=tool_call_id)], details={})

    tools = [
        AgentTool(
            name="slow",
            label="Slow",
            description="Slow",
            parameters={"type": "object", "properties": {}},
            execute=execute_slow,
        ),
        AgentTool(
            name="fast",
            label="Fast",
            description="Fast",
            parameters={"type": "object", "properties": {}},
            execute=execute_fast,
        ),
    ]
    first = _assistant(
        [
            ToolCall(id="slow-call", name="slow", arguments={}),
            ToolCall(id="fast-call", name="fast", arguments={}),
        ],
        stop_reason="toolUse",
    )
    second = _assistant([TextContent(text="done")])
    calls = 0

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        nonlocal calls
        calls += 1
        return _stream_for(first if calls == 1 else second)

    stream = agent_loop(
        [_user()],
        AgentContext(messages=[], tools=tools),
        AgentLoopConfig(model=_model()),
        stream_fn=stream_fn,
    )
    events = [event async for event in stream]
    result = await stream.result()

    tool_end_ids = [
        event.tool_call_id for event in events if event.type == "tool_execution_end"
    ]
    tool_result_ids = [message.tool_call_id for message in result if message.role == "toolResult"]

    assert completion_order == ["fast-call", "slow-call"]
    assert tool_end_ids == ["fast-call", "slow-call"]
    assert tool_result_ids == ["slow-call", "fast-call"]


@pytest.mark.asyncio
async def test_tool_errors_blocks_and_after_hook_override() -> None:
    async def execute(tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="original")], details={"old": True})

    async def before(context: BeforeToolCallContext, signal=None):
        if context.tool_call.name == "blocked":
            return {"block": True, "reason": "blocked by policy"}
        return None

    async def after(context: AfterToolCallContext, signal=None):
        if context.tool_call.name == "ok":
            return {
                "content": [TextContent(text="overridden")],
                "details": {"new": True},
                "isError": False,
                "terminate": True,
            }
        return None

    tools = [
        AgentTool(
            name="ok",
            label="OK",
            description="OK",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "number"}},
                "required": ["x"],
            },
            execute=execute,
        ),
        AgentTool(
            name="blocked",
            label="Blocked",
            description="Blocked",
            parameters={"type": "object", "properties": {}},
            execute=execute,
        ),
    ]
    first = _assistant(
        [
            ToolCall(id="ok-call", name="ok", arguments={"x": 1}),
            ToolCall(id="invalid-call", name="ok", arguments={}),
            ToolCall(id="blocked-call", name="blocked", arguments={}),
            ToolCall(id="missing-call", name="missing", arguments={}),
        ],
        stop_reason="toolUse",
    )
    second = _assistant([TextContent(text="done")])
    calls = 0

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        nonlocal calls
        calls += 1
        return _stream_for(first if calls == 1 else second)

    stream = agent_loop(
        [_user()],
        AgentContext(messages=[], tools=tools),
        AgentLoopConfig(model=_model(), before_tool_call=before, after_tool_call=after),
        stream_fn=stream_fn,
    )
    result = await stream.result()

    tool_results = [message for message in result if message.role == "toolResult"]

    assert tool_results[0].content[0].text == "overridden"
    assert tool_results[0].details == {"new": True}
    assert tool_results[0].is_error is False
    assert "Validation failed" in tool_results[1].content[0].text
    assert tool_results[1].is_error is True
    assert tool_results[2].content[0].text == "blocked by policy"
    assert tool_results[2].is_error is True
    assert tool_results[3].content[0].text == "Tool missing not found"
    assert tool_results[3].is_error is True
