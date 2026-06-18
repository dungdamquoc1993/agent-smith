"""Agent loop orchestration."""

from __future__ import annotations

from typing import Any

from agent_smith.ai.types import ToolResultMessage
from agent_smith.agent.agent_loop.streaming import stream_assistant_response
from agent_smith.agent.agent_loop.tools import execute_tool_calls, get_tool_calls
from agent_smith.agent.agent_loop.utils import (
    call_maybe,
    coerce_turn_update,
    emit,
    get_messages,
    next_reasoning,
)
from agent_smith.agent.events import AgentEventStream, create_agent_event_stream
from agent_smith.agent.types import (
    AgentContext,
    AgentEndEvent,
    AgentEventSink,
    AgentLoopConfig,
    AgentMessage,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
    StreamFn,
    TurnEndEvent,
    TurnStartEvent,
)


def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> AgentEventStream:
    stream = create_agent_event_stream()

    async def produce() -> None:
        try:
            messages = await run_agent_loop(
                prompts,
                context,
                config,
                stream.push,
                signal,
                stream_fn,
            )
            stream.end(messages)
        except Exception as exc:  # pragma: no cover - defensive stream plumbing
            stream.fail(exc)

    stream.set_producer(produce())
    return stream


def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> AgentEventStream:
    validate_continue_context(context)
    stream = create_agent_event_stream()

    async def produce() -> None:
        try:
            messages = await run_agent_loop_continue(
                context,
                config,
                stream.push,
                signal,
                stream_fn,
            )
            stream.end(messages)
        except Exception as exc:  # pragma: no cover - defensive stream plumbing
            stream.fail(exc)

    stream.set_producer(produce())
    return stream


async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit_event: AgentEventSink,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> list[AgentMessage]:
    new_messages = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=context.tools,
    )

    await emit(emit_event, AgentStartEvent())
    await emit(emit_event, TurnStartEvent())
    for prompt in prompts:
        await emit(emit_event, MessageStartEvent(message=prompt))
        await emit(emit_event, MessageEndEvent(message=prompt))

    await run_loop(current_context, new_messages, config, signal, emit_event, stream_fn)
    return new_messages


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    emit_event: AgentEventSink,
    signal: Any | None = None,
    stream_fn: StreamFn | None = None,
) -> list[AgentMessage]:
    validate_continue_context(context)
    new_messages: list[AgentMessage] = []
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=context.tools,
    )

    await emit(emit_event, AgentStartEvent())
    await emit(emit_event, TurnStartEvent())

    await run_loop(current_context, new_messages, config, signal, emit_event, stream_fn)
    return new_messages


def validate_continue_context(context: AgentContext) -> None:
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    if context.messages[-1].role == "assistant":
        raise ValueError("Cannot continue from message role: assistant")


async def run_loop(
    initial_context: AgentContext,
    new_messages: list[AgentMessage],
    initial_config: AgentLoopConfig,
    signal: Any | None,
    emit_event: AgentEventSink,
    stream_fn: StreamFn | None,
) -> None:
    current_context = initial_context
    config = initial_config
    first_turn = True
    pending_messages = await get_messages(config.get_steering_messages)

    while True:
        has_more_tool_calls = True

        while has_more_tool_calls or pending_messages:
            if not first_turn:
                await emit(emit_event, TurnStartEvent())
            else:
                first_turn = False

            if pending_messages:
                for message in pending_messages:
                    await emit(emit_event, MessageStartEvent(message=message))
                    await emit(emit_event, MessageEndEvent(message=message))
                    current_context.messages.append(message)
                    new_messages.append(message)
                pending_messages = []

            message = await stream_assistant_response(
                current_context,
                config,
                signal,
                emit_event,
                stream_fn,
            )
            new_messages.append(message)

            if message.stop_reason in ("error", "aborted"):
                await emit(emit_event, TurnEndEvent(message=message, tool_results=[]))
                await emit(emit_event, AgentEndEvent(messages=new_messages))
                return

            tool_results: list[ToolResultMessage] = []
            has_more_tool_calls = False
            if get_tool_calls(message):
                executed_batch = await execute_tool_calls(
                    current_context,
                    message,
                    config,
                    signal,
                    emit_event,
                )
                tool_results.extend(executed_batch.messages)
                has_more_tool_calls = not executed_batch.terminate

                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            await emit(emit_event, TurnEndEvent(message=message, tool_results=tool_results))

            next_turn_context = PrepareNextTurnContext(
                message=message,
                tool_results=tool_results,
                context=current_context,
                new_messages=new_messages,
            )
            next_turn_snapshot = await call_maybe(config.prepare_next_turn, next_turn_context)
            if next_turn_snapshot is not None:
                update = coerce_turn_update(next_turn_snapshot)
                current_context = update.context or current_context
                config = config.model_copy(
                    update={
                        "model": update.model or config.model,
                        "reasoning": next_reasoning(config.reasoning, update.thinking_level),
                    }
                )

            should_stop = await call_maybe(
                config.should_stop_after_turn,
                ShouldStopAfterTurnContext(
                    message=message,
                    tool_results=tool_results,
                    context=current_context,
                    new_messages=new_messages,
                ),
            )
            if should_stop:
                await emit(emit_event, AgentEndEvent(messages=new_messages))
                return

            pending_messages = await get_messages(config.get_steering_messages)

        follow_up_messages = await get_messages(config.get_follow_up_messages)
        if follow_up_messages:
            pending_messages = follow_up_messages
            continue

        break

    await emit(emit_event, AgentEndEvent(messages=new_messages))
