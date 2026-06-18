"""Agent loop types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_smith.ai.events import AssistantMessageEventStream
from agent_smith.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    ImageContent,
    Message,
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


class SignalLike:
    """Small structural base for abort-like objects."""

    aborted: bool

    def is_set(self) -> bool: ...


AgentToolUpdateCallback = Callable[["AgentToolResult"], None]
AgentToolExecute = Callable[
    [str, Any, Any | None, AgentToolUpdateCallback | None],
    Awaitable[Any] | Any,
]
PrepareArguments = Callable[[Any], Any]


class AgentToolResult(BaseModel):
    content: list[TextContent | ImageContent]
    details: Any | None = None
    terminate: bool | None = None

    model_config = {"populate_by_name": True}


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
    details: Any | None = None
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
    args: Any
    context: AgentContext

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}


class AfterToolCallContext(BeforeToolCallContext):
    result: AgentToolResult
    is_error: bool = Field(alias="isError")

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}


ConvertToLlmFn = Callable[[list[AgentMessage]], list[Message] | Awaitable[list[Message]]]
TransformContextFn = Callable[
    [list[AgentMessage], Any | None],
    list[AgentMessage] | Awaitable[list[AgentMessage]],
]
GetApiKeyFn = Callable[[str], str | None | Awaitable[str | None]]
ShouldStopAfterTurnFn = Callable[
    [ShouldStopAfterTurnContext],
    bool | Awaitable[bool],
]
PrepareNextTurnFn = Callable[
    [PrepareNextTurnContext],
    AgentLoopTurnUpdate | dict[str, Any] | None | Awaitable[AgentLoopTurnUpdate | dict[str, Any] | None],
]
GetMessagesFn = Callable[[], list[AgentMessage] | Awaitable[list[AgentMessage]]]
BeforeToolCallFn = Callable[
    [BeforeToolCallContext, Any | None],
    BeforeToolCallResult | dict[str, Any] | None | Awaitable[BeforeToolCallResult | dict[str, Any] | None],
]
AfterToolCallFn = Callable[
    [AfterToolCallContext, Any | None],
    AfterToolCallResult | dict[str, Any] | None | Awaitable[AfterToolCallResult | dict[str, Any] | None],
]
StreamFn = Callable[
    [Model, Context, SimpleStreamOptions | None],
    AssistantMessageEventStream | Awaitable[AssistantMessageEventStream],
]
AgentEventSink = Callable[["AgentEvent"], None | Awaitable[None]]


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
    args: Any

    model_config = {"populate_by_name": True}


class ToolExecutionUpdateEvent(BaseModel):
    type: Literal["tool_execution_update"] = "tool_execution_update"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: Any
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
