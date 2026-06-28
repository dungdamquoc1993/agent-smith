"""Tool-call execution for the agent loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai.types import AssistantMessage, HookPayload, JsonValue, TextContent, ToolCall, ToolResultMessage
from agent.agent_loop.utils import call, call_maybe, emit, is_aborted, now_ms
from agent.types import (
    AbortSignal,
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEventSink,
    AgentLoopConfig,
    AgentTool,
    AgentToolCall,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    MessageEndEvent,
    MessageStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from agent.validation import validate_tool_arguments


@dataclass
class ExecutedToolCallBatch:
    messages: list[ToolResultMessage]
    terminate: bool


@dataclass
class PreparedToolCall:
    tool_call: AgentToolCall
    tool: AgentTool
    args: JsonValue


@dataclass
class ImmediateToolCallOutcome:
    result: AgentToolResult
    is_error: bool


@dataclass
class ExecutedToolCallOutcome:
    result: AgentToolResult
    is_error: bool


@dataclass
class FinalizedToolCallOutcome:
    tool_call: AgentToolCall
    result: AgentToolResult
    is_error: bool


async def execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    signal: AbortSignal | None,
    emit_event: AgentEventSink,
) -> ExecutedToolCallBatch:
    tool_calls = get_tool_calls(assistant_message)
    has_sequential_tool_call = any(
        (find_tool(current_context.tools, tool_call.name).execution_mode == "sequential")
        if find_tool(current_context.tools, tool_call.name)
        else False
        for tool_call in tool_calls
    )
    if config.tool_execution == "sequential" or has_sequential_tool_call:
        return await execute_tool_calls_sequential(
            current_context,
            assistant_message,
            tool_calls,
            config,
            signal,
            emit_event,
        )
    return await execute_tool_calls_parallel(
        current_context,
        assistant_message,
        tool_calls,
        config,
        signal,
        emit_event,
    )


async def execute_tool_calls_sequential(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[AgentToolCall],
    config: AgentLoopConfig,
    signal: AbortSignal | None,
    emit_event: AgentEventSink,
) -> ExecutedToolCallBatch:
    finalized_calls: list[FinalizedToolCallOutcome] = []
    messages: list[ToolResultMessage] = []

    for tool_call in tool_calls:
        await emit_tool_execution_start(tool_call, emit_event)
        preparation = await prepare_tool_call(
            current_context,
            assistant_message,
            tool_call,
            config,
            signal,
        )
        if isinstance(preparation, ImmediateToolCallOutcome):
            finalized = FinalizedToolCallOutcome(
                tool_call=tool_call,
                result=preparation.result,
                is_error=preparation.is_error,
            )
        else:
            executed = await execute_prepared_tool_call(preparation, signal, emit_event)
            finalized = await finalize_executed_tool_call(
                current_context,
                assistant_message,
                preparation,
                executed,
                config,
                signal,
            )

        await emit_tool_execution_end(finalized, emit_event)
        tool_result_message = create_tool_result_message(finalized)
        await emit_tool_result_message(tool_result_message, emit_event)
        finalized_calls.append(finalized)
        messages.append(tool_result_message)

        if is_aborted(signal):
            break

    return ExecutedToolCallBatch(
        messages=messages,
        terminate=should_terminate_tool_batch(finalized_calls),
    )


async def execute_tool_calls_parallel(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[AgentToolCall],
    config: AgentLoopConfig,
    signal: AbortSignal | None,
    emit_event: AgentEventSink,
) -> ExecutedToolCallBatch:
    finalized_entries: list[FinalizedToolCallOutcome | asyncio.Task[FinalizedToolCallOutcome]] = []

    for tool_call in tool_calls:
        await emit_tool_execution_start(tool_call, emit_event)
        preparation = await prepare_tool_call(
            current_context,
            assistant_message,
            tool_call,
            config,
            signal,
        )
        if isinstance(preparation, ImmediateToolCallOutcome):
            finalized = FinalizedToolCallOutcome(
                tool_call=tool_call,
                result=preparation.result,
                is_error=preparation.is_error,
            )
            await emit_tool_execution_end(finalized, emit_event)
            finalized_entries.append(finalized)
            if is_aborted(signal):
                break
            continue

        finalized_entries.append(
            asyncio.create_task(
                run_prepared_tool_call(
                    current_context,
                    assistant_message,
                    preparation,
                    config,
                    signal,
                    emit_event,
                )
            )
        )
        if is_aborted(signal):
            break

    ordered_finalized_calls: list[FinalizedToolCallOutcome] = []
    for entry in finalized_entries:
        if isinstance(entry, asyncio.Task):
            ordered_finalized_calls.append(await entry)
        else:
            ordered_finalized_calls.append(entry)

    messages: list[ToolResultMessage] = []
    for finalized in ordered_finalized_calls:
        tool_result_message = create_tool_result_message(finalized)
        await emit_tool_result_message(tool_result_message, emit_event)
        messages.append(tool_result_message)

    return ExecutedToolCallBatch(
        messages=messages,
        terminate=should_terminate_tool_batch(ordered_finalized_calls),
    )


async def run_prepared_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    preparation: PreparedToolCall,
    config: AgentLoopConfig,
    signal: AbortSignal | None,
    emit_event: AgentEventSink,
) -> FinalizedToolCallOutcome:
    executed = await execute_prepared_tool_call(preparation, signal, emit_event)
    finalized = await finalize_executed_tool_call(
        current_context,
        assistant_message,
        preparation,
        executed,
        config,
        signal,
    )
    await emit_tool_execution_end(finalized, emit_event)
    return finalized


async def prepare_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_call: AgentToolCall,
    config: AgentLoopConfig,
    signal: AbortSignal | None,
) -> PreparedToolCall | ImmediateToolCallOutcome:
    tool = find_tool(current_context.tools, tool_call.name)
    if tool is None:
        return ImmediateToolCallOutcome(
            result=create_error_tool_result(f"Tool {tool_call.name} not found"),
            is_error=True,
        )

    try:
        prepared_tool_call = await prepare_tool_call_arguments(tool, tool_call)
        validated_args = validate_tool_arguments(tool, prepared_tool_call)
        if config.before_tool_call:
            before_result = await call_maybe(
                config.before_tool_call,
                BeforeToolCallContext(
                    assistant_message=assistant_message,
                    tool_call=tool_call,
                    args=validated_args,
                    context=current_context,
                ),
                signal,
            )
            if is_aborted(signal):
                return ImmediateToolCallOutcome(
                    result=create_error_tool_result("Operation aborted"),
                    is_error=True,
                )
            if before_result is not None:
                before = coerce_before_tool_call_result(before_result)
                if before.block:
                    return ImmediateToolCallOutcome(
                        result=create_error_tool_result(
                            before.reason or "Tool execution was blocked"
                        ),
                        is_error=True,
                    )
                if before.updated_args is not None:
                    validated_args = validate_tool_arguments(
                        tool,
                        tool_call.model_copy(update={"arguments": before.updated_args}),
                    )
        if is_aborted(signal):
            return ImmediateToolCallOutcome(
                result=create_error_tool_result("Operation aborted"),
                is_error=True,
            )
        return PreparedToolCall(tool_call=tool_call, tool=tool, args=validated_args)
    except Exception as exc:
        return ImmediateToolCallOutcome(result=create_error_tool_result(str(exc)), is_error=True)


async def prepare_tool_call_arguments(tool: AgentTool, tool_call: AgentToolCall) -> AgentToolCall:
    if not tool.prepare_arguments:
        return tool_call
    prepared_arguments = await call(tool.prepare_arguments(tool_call.arguments))
    if prepared_arguments is tool_call.arguments:
        return tool_call
    return tool_call.model_copy(update={"arguments": prepared_arguments})


async def execute_prepared_tool_call(
    prepared: PreparedToolCall,
    signal: AbortSignal | None,
    emit_event: AgentEventSink,
) -> ExecutedToolCallOutcome:
    update_tasks: list[asyncio.Task[None]] = []
    accepting_updates = True

    def on_update(partial_result: AgentToolResult | dict[str, HookPayload]) -> None:
        nonlocal accepting_updates
        if not accepting_updates:
            return
        result = coerce_tool_result(partial_result)
        update_tasks.append(
            asyncio.create_task(
                emit(
                    emit_event,
                    ToolExecutionUpdateEvent(
                        tool_call_id=prepared.tool_call.id,
                        tool_name=prepared.tool_call.name,
                        args=prepared.tool_call.arguments,
                        partial_result=result,
                    ),
                )
            )
        )

    try:
        result = await call(
            prepared.tool.execute(prepared.tool_call.id, prepared.args, signal, on_update)
        )
        accepting_updates = False
        if update_tasks:
            await asyncio.gather(*update_tasks)
        return ExecutedToolCallOutcome(result=coerce_tool_result(result), is_error=False)
    except Exception as exc:
        accepting_updates = False
        if update_tasks:
            await asyncio.gather(*update_tasks)
        return ExecutedToolCallOutcome(result=create_error_tool_result(str(exc)), is_error=True)
    finally:
        accepting_updates = False


async def finalize_executed_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    prepared: PreparedToolCall,
    executed: ExecutedToolCallOutcome,
    config: AgentLoopConfig,
    signal: AbortSignal | None,
) -> FinalizedToolCallOutcome:
    result = executed.result
    is_error = executed.is_error

    if config.after_tool_call:
        try:
            after_result = await call_maybe(
                config.after_tool_call,
                AfterToolCallContext(
                    assistant_message=assistant_message,
                    tool_call=prepared.tool_call,
                    args=prepared.args,
                    result=result,
                    is_error=is_error,
                    context=current_context,
                ),
                signal,
            )
            if after_result is not None:
                result, is_error = apply_after_tool_call_result(result, is_error, after_result)
        except Exception as exc:
            result = create_error_tool_result(str(exc))
            is_error = True

    return FinalizedToolCallOutcome(tool_call=prepared.tool_call, result=result, is_error=is_error)


def apply_after_tool_call_result(
    result: AgentToolResult,
    is_error: bool,
    after_result: AfterToolCallResult | dict[str, HookPayload],
) -> tuple[AgentToolResult, bool]:
    after = coerce_after_tool_call_result(after_result)
    updates: dict[str, HookPayload] = {}
    if "content" in after.model_fields_set:
        updates["content"] = after.content
    if "details" in after.model_fields_set:
        updates["details"] = after.details
    if "terminate" in after.model_fields_set:
        updates["terminate"] = after.terminate
    if updates:
        result = result.model_copy(update=updates)
    if "is_error" in after.model_fields_set:
        is_error = bool(after.is_error)
    return result, is_error


def should_terminate_tool_batch(finalized_calls: list[FinalizedToolCallOutcome]) -> bool:
    return bool(finalized_calls) and all(finalized.result.terminate is True for finalized in finalized_calls)


def create_error_tool_result(message: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=message)], details={})


async def emit_tool_execution_start(tool_call: AgentToolCall, emit_event: AgentEventSink) -> None:
    await emit(
        emit_event,
        ToolExecutionStartEvent(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            args=tool_call.arguments,
        ),
    )


async def emit_tool_execution_end(
    finalized: FinalizedToolCallOutcome,
    emit_event: AgentEventSink,
) -> None:
    await emit(
        emit_event,
        ToolExecutionEndEvent(
            tool_call_id=finalized.tool_call.id,
            tool_name=finalized.tool_call.name,
            result=finalized.result,
            is_error=finalized.is_error,
        ),
    )


def create_tool_result_message(finalized: FinalizedToolCallOutcome) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=finalized.tool_call.id,
        tool_name=finalized.tool_call.name,
        content=finalized.result.content,
        details=finalized.result.details,
        is_error=finalized.is_error,
        timestamp=now_ms(),
    )


async def emit_tool_result_message(
    tool_result_message: ToolResultMessage,
    emit_event: AgentEventSink,
) -> None:
    await emit(emit_event, MessageStartEvent(message=tool_result_message))
    await emit(emit_event, MessageEndEvent(message=tool_result_message))


def get_tool_calls(message: AssistantMessage) -> list[AgentToolCall]:
    return [block for block in message.content if isinstance(block, ToolCall)]


def find_tool(tools: list[AgentTool] | None, name: str) -> AgentTool | None:
    return next((tool for tool in tools or [] if tool.name == name), None)


def coerce_tool_result(result: AgentToolResult | dict[str, HookPayload]) -> AgentToolResult:
    if isinstance(result, AgentToolResult):
        return result
    return AgentToolResult.model_validate(result)


def coerce_before_tool_call_result(
    result: BeforeToolCallResult | dict[str, HookPayload],
) -> BeforeToolCallResult:
    if isinstance(result, BeforeToolCallResult):
        return result
    return BeforeToolCallResult.model_validate(result)


def coerce_after_tool_call_result(
    result: AfterToolCallResult | dict[str, HookPayload],
) -> AfterToolCallResult:
    if isinstance(result, AfterToolCallResult):
        return result
    return AfterToolCallResult.model_validate(result)
