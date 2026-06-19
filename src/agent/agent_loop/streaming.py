"""Assistant response streaming for the agent loop."""

from __future__ import annotations

import inspect
from typing import Any

from agent_smith.ai import stream_simple
from agent_smith.ai.types import AssistantMessage, Context, SimpleStreamOptions
from agent_smith.agent.agent_loop.utils import call, call_maybe, emit
from agent_smith.agent.types import (
    AbortSignal,
    AgentContext,
    AgentEventSink,
    AgentLoopConfig,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    StreamFn,
)

UPDATE_EVENT_TYPES = {
    "text_start",
    "text_delta",
    "text_end",
    "thinking_start",
    "thinking_delta",
    "thinking_end",
    "toolcall_start",
    "toolcall_delta",
    "toolcall_end",
}


async def stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: AbortSignal | None,
    emit_event: AgentEventSink,
    stream_fn: StreamFn | None,
) -> AssistantMessage:
    messages = context.messages
    if config.transform_context:
        messages = await call(config.transform_context(messages, signal))

    llm_messages = await call(config.convert_to_llm(messages))
    llm_context = Context(
        system_prompt=context.system_prompt,
        messages=llm_messages,
        tools=context.tools,
    )

    stream_function = stream_fn or stream_simple
    resolved_api_key = (
        await call_maybe(config.get_api_key, config.model.provider)
        if config.get_api_key
        else None
    ) or config.api_key
    response = stream_function(
        config.model,
        llm_context,
        to_simple_stream_options(config, resolved_api_key),
    )
    if inspect.isawaitable(response):
        response = await response

    partial_message: AssistantMessage | None = None
    added_partial = False

    async for event in response:
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)
            added_partial = True
            await emit(emit_event, MessageStartEvent(message=partial_message.model_copy(deep=True)))
        elif event.type in UPDATE_EVENT_TYPES and partial_message is not None:
            partial_message = event.partial
            context.messages[-1] = partial_message
            await emit(
                emit_event,
                MessageUpdateEvent(
                    assistant_message_event=event,
                    message=partial_message.model_copy(deep=True),
                ),
            )
        elif event.type in ("done", "error"):
            return await finish_assistant_response(response, context, added_partial, emit_event)

    return await finish_assistant_response(response, context, added_partial, emit_event)


async def finish_assistant_response(
    response: Any,
    context: AgentContext,
    added_partial: bool,
    emit_event: AgentEventSink,
) -> AssistantMessage:
    final_message = await response.result()
    if added_partial:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await emit(emit_event, MessageStartEvent(message=final_message.model_copy(deep=True)))
    await emit(emit_event, MessageEndEvent(message=final_message))
    return final_message


def to_simple_stream_options(config: AgentLoopConfig, api_key: str | None) -> SimpleStreamOptions:
    values = {
        name: getattr(config, name)
        for name in SimpleStreamOptions.model_fields
        if hasattr(config, name)
    }
    values.update(config.model_extra or {})
    values["api_key"] = api_key
    return SimpleStreamOptions(**values)
