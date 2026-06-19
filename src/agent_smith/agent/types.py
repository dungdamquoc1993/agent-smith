"""Agent loop types."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from agent_smith.ai.events import AssistantMessageEventStream
from agent_smith.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    HookPayload,
    ImageContent,
    JsonObject,
    JsonValue,
    Message,
    MaybeAwaitable,
    Model,
    ModelThinkingLevel,
    SimpleStreamOptions,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
)

AgentMessage = Message
AgentToolCall = ToolCall
ToolExecutionMode = Literal["sequential", "parallel"]


class AbortSignal(Protocol):
    def is_set(self) -> bool: ...


class AgentToolResult(BaseModel):
    content: list[TextContent | ImageContent]
    details: HookPayload | None = None
    terminate: bool | None = None

    model_config = {"populate_by_name": True}


AgentToolUpdateCallback = Callable[[AgentToolResult | dict[str, HookPayload]], None]
AgentToolExecute = Callable[
    [str, JsonValue, AbortSignal | None, AgentToolUpdateCallback | None],
    MaybeAwaitable[AgentToolResult | dict[str, HookPayload]],
]
PrepareArguments = Callable[[JsonObject], MaybeAwaitable[JsonObject]]


class AgentTool(Tool):
    label: str
    prepare_arguments: PrepareArguments | None = Field(
        default=None,
        alias="prepareArguments",
        exclude=True,
    )
    execute: AgentToolExecute = Field(exclude=True)
    execution_mode: ToolExecutionMode | None = Field(default=None, alias="executionMode")

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }


class AgentContext(BaseModel):
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    messages: list[AgentMessage] = Field(default_factory=list)
    tools: list[AgentTool] | None = None

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    return messages


class BeforeToolCallResult(BaseModel):
    block: bool | None = None
    reason: str | None = None


class AfterToolCallResult(BaseModel):
    content: list[TextContent | ImageContent] | None = None
    details: HookPayload | None = None
    is_error: bool | None = Field(default=None, alias="isError")
    terminate: bool | None = None

    model_config = {"populate_by_name": True}


class ShouldStopAfterTurnContext(BaseModel):
    message: AssistantMessage
    tool_results: list[ToolResultMessage] = Field(alias="toolResults")
    context: AgentContext
    new_messages: list[AgentMessage] = Field(alias="newMessages")

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}


class AgentLoopTurnUpdate(BaseModel):
    context: AgentContext | None = None
    model: Model | None = None
    thinking_level: ModelThinkingLevel | None = Field(default=None, alias="thinkingLevel")

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}


PrepareNextTurnContext = ShouldStopAfterTurnContext


class BeforeToolCallContext(BaseModel):
    assistant_message: AssistantMessage = Field(alias="assistantMessage")
    tool_call: AgentToolCall = Field(alias="toolCall")
    args: JsonValue
    context: AgentContext

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}


class AfterToolCallContext(BeforeToolCallContext):
    result: AgentToolResult
    is_error: bool = Field(alias="isError")

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}


ConvertToLlmFn = Callable[[list[AgentMessage]], MaybeAwaitable[list[Message]]]
TransformContextFn = Callable[
    [list[AgentMessage], AbortSignal | None],
    MaybeAwaitable[list[AgentMessage]],
]
GetApiKeyFn = Callable[[str], MaybeAwaitable[str | None]]
ShouldStopAfterTurnFn = Callable[
    [ShouldStopAfterTurnContext],
    MaybeAwaitable[bool],
]
PrepareNextTurnFn = Callable[
    [PrepareNextTurnContext],
    MaybeAwaitable[AgentLoopTurnUpdate | dict[str, HookPayload] | None],
]
GetMessagesFn = Callable[[], MaybeAwaitable[list[AgentMessage]]]
BeforeToolCallFn = Callable[
    [BeforeToolCallContext, AbortSignal | None],
    MaybeAwaitable[BeforeToolCallResult | dict[str, HookPayload] | None],
]
AfterToolCallFn = Callable[
    [AfterToolCallContext, AbortSignal | None],
    MaybeAwaitable[AfterToolCallResult | dict[str, HookPayload] | None],
]
StreamFn = Callable[
    [Model, Context, SimpleStreamOptions | None],
    MaybeAwaitable[AssistantMessageEventStream],
]
AgentEventSink = Callable[["AgentEvent"], MaybeAwaitable[None]]


class AgentLoopConfig(SimpleStreamOptions):
    model: Model
    convert_to_llm: ConvertToLlmFn = Field(
        default=default_convert_to_llm,
        alias="convertToLlm",
        exclude=True,
    )
    transform_context: TransformContextFn | None = Field(
        default=None,
        alias="transformContext",
        exclude=True,
    )
    get_api_key: GetApiKeyFn | None = Field(default=None, alias="getApiKey", exclude=True)
    should_stop_after_turn: ShouldStopAfterTurnFn | None = Field(
        default=None,
        alias="shouldStopAfterTurn",
        exclude=True,
    )
    prepare_next_turn: PrepareNextTurnFn | None = Field(
        default=None,
        alias="prepareNextTurn",
        exclude=True,
    )
    get_steering_messages: GetMessagesFn | None = Field(
        default=None,
        alias="getSteeringMessages",
        exclude=True,
    )
    get_follow_up_messages: GetMessagesFn | None = Field(
        default=None,
        alias="getFollowUpMessages",
        exclude=True,
    )
    tool_execution: ToolExecutionMode | None = Field(default=None, alias="toolExecution")
    before_tool_call: BeforeToolCallFn | None = Field(
        default=None,
        alias="beforeToolCall",
        exclude=True,
    )
    after_tool_call: AfterToolCallFn | None = Field(
        default=None,
        alias="afterToolCall",
        exclude=True,
    )

    model_config = {
        "populate_by_name": True,
        "extra": "allow",
        "arbitrary_types_allowed": True,
    }


class AgentStartEvent(BaseModel):
    type: Literal["agent_start"] = "agent_start"


class AgentEndEvent(BaseModel):
    type: Literal["agent_end"] = "agent_end"
    messages: list[AgentMessage]


class TurnStartEvent(BaseModel):
    type: Literal["turn_start"] = "turn_start"


class TurnEndEvent(BaseModel):
    type: Literal["turn_end"] = "turn_end"
    message: AgentMessage
    tool_results: list[ToolResultMessage] = Field(alias="toolResults")

    model_config = {"populate_by_name": True}


class MessageStartEvent(BaseModel):
    type: Literal["message_start"] = "message_start"
    message: AgentMessage


class MessageUpdateEvent(BaseModel):
    type: Literal["message_update"] = "message_update"
    message: AgentMessage
    assistant_message_event: AssistantMessageEvent = Field(alias="assistantMessageEvent")

    model_config = {"populate_by_name": True}


class MessageEndEvent(BaseModel):
    type: Literal["message_end"] = "message_end"
    message: AgentMessage


class ToolExecutionStartEvent(BaseModel):
    type: Literal["tool_execution_start"] = "tool_execution_start"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: JsonValue

    model_config = {"populate_by_name": True}


class ToolExecutionUpdateEvent(BaseModel):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: JsonValue
    partial_result: AgentToolResult = Field(alias="partialResult")

    model_config = {"populate_by_name": True}


class ToolExecutionEndEvent(BaseModel):
    type: Literal["tool_execution_end"] = "tool_execution_end"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    result: AgentToolResult
    is_error: bool = Field(alias="isError")

    model_config = {"populate_by_name": True}


AgentEvent = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
)
