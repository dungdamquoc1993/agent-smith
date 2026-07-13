"""Unified LLM types - ported from pi packages/ai/src/types.ts."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Literal, TypeAlias, TypeVar

from pydantic import BaseModel, Field, JsonValue as PydanticJsonValue

# --- API / Provider identifiers ---

KnownApi = Literal["litellm"]
Api = str  # KnownApi | custom

KnownProvider = Literal[
    "openai",
    "anthropic",
    "google",
    "openrouter",
]
Provider = str  # KnownProvider | custom

ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]
ModelThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]
CacheRetention = Literal["none", "short", "long"]
ProviderEnv = dict[str, str]
JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = PydanticJsonValue
JsonObject: TypeAlias = dict[str, JsonValue]
ProviderPayload: TypeAlias = Any
HookPayload: TypeAlias = Any
T = TypeVar("T")
MaybeAwaitable: TypeAlias = T | Awaitable[T]


# --- Content blocks ---


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = Field(default=None, alias="textSignature")

    model_config = {"populate_by_name": True}


class ThinkingContent(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    thinking_signature: str | None = Field(default=None, alias="thinkingSignature")
    redacted: bool | None = None

    model_config = {"populate_by_name": True}


class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str  # base64
    mime_type: str = Field(alias="mimeType")

    model_config = {"populate_by_name": True}


class ToolCall(BaseModel):
    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: JsonObject = Field(default_factory=dict)
    thought_signature: str | None = Field(default=None, alias="thoughtSignature")

    model_config = {"populate_by_name": True}


AssistantContentBlock = TextContent | ThinkingContent | ToolCall
ToolResultContentBlock = TextContent | ImageContent


# --- Usage / Cost ---


class UsageCost(BaseModel):
    input: float = 0.0
    output: float = 0.0
    cache_read: float = Field(default=0.0, alias="cacheRead")
    cache_write: float = Field(default=0.0, alias="cacheWrite")
    total: float = 0.0

    model_config = {"populate_by_name": True}


class Usage(BaseModel):
    input: int = 0
    output: int = 0
    cache_read: int = Field(default=0, alias="cacheRead")
    cache_write: int = Field(default=0, alias="cacheWrite")
    cache_write_1h: int | None = Field(default=None, alias="cacheWrite1h")
    total_tokens: int = Field(default=0, alias="totalTokens")
    cost: UsageCost = Field(default_factory=UsageCost)

    model_config = {"populate_by_name": True}


# --- Messages ---


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[TextContent | ImageContent]
    timestamp: int  # unix ms


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[AssistantContentBlock] = Field(default_factory=list)
    api: Api
    provider: Provider
    model: str
    response_model: str | None = Field(default=None, alias="responseModel")
    response_id: str | None = Field(default=None, alias="responseId")
    usage: Usage = Field(default_factory=Usage)
    stop_reason: StopReason = Field(default="stop", alias="stopReason")
    error_message: str | None = Field(default=None, alias="errorMessage")
    timestamp: int

    model_config = {"populate_by_name": True}


class ToolResultMessage(BaseModel):
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    content: list[ToolResultContentBlock]
    details: HookPayload | None = None
    is_error: bool = Field(default=False, alias="isError")
    timestamp: int

    model_config = {"populate_by_name": True}


Message = UserMessage | AssistantMessage | ToolResultMessage


# --- Tools & Context ---


class Tool(BaseModel):
    name: str
    description: str
    parameters: JsonObject  # JSON Schema


class Context(BaseModel):
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    messages: list[Message] = Field(default_factory=list)
    tools: list[Tool] | None = None

    model_config = {"populate_by_name": True}


# --- Model metadata ---


class ModelCost(BaseModel):
    input: float = 0.0  # $/million tokens
    output: float = 0.0
    cache_read: float = Field(default=0.0, alias="cacheRead")
    cache_write: float = Field(default=0.0, alias="cacheWrite")

    model_config = {"populate_by_name": True}


class Model(BaseModel):
    key: str | None = None
    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str = Field(default="", alias="baseUrl")
    reasoning: bool = False
    input: list[Literal["text", "image"]] = Field(default_factory=lambda: ["text"])
    cost: ModelCost = Field(default_factory=ModelCost)
    context_window: int = Field(default=128_000, alias="contextWindow")
    max_tokens: int = Field(default=16_384, alias="maxTokens")
    headers: dict[str, str] | None = None
    provider_options: JsonObject | None = Field(default=None, alias="providerOptions")
    compat: JsonObject | None = None
    thinking_level_map: dict[ModelThinkingLevel, str | None] | None = Field(
        default=None,
        alias="thinkingLevelMap",
    )
    litellm_model: str | None = Field(
        default=None,
        description="LiteLLM model id override (e.g. openrouter/openai/gpt-5.5)",
    )

    model_config = {"populate_by_name": True}

    def resolve_litellm_model(self) -> str:
        if self.litellm_model:
            return self.litellm_model
        return self.id


# --- Stream options ---


class StreamOptions(BaseModel):
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    api_key: str | None = Field(default=None, alias="apiKey")
    cache_retention: CacheRetention | None = Field(default=None, alias="cacheRetention")
    session_id: str | None = Field(default=None, alias="sessionId")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    max_retries: int | None = Field(default=None, alias="maxRetries")
    max_retry_delay_ms: int | None = Field(default=None, alias="maxRetryDelayMs")
    headers: dict[str, str] | None = None
    metadata: JsonObject | None = None
    env: ProviderEnv | None = None
    provider_options: JsonObject | None = Field(default=None, alias="providerOptions")

    model_config = {"populate_by_name": True, "extra": "allow"}


class SimpleStreamOptions(StreamOptions):
    reasoning: ThinkingLevel | None = None


# --- Assistant message events ---


class AssistantMessageEventStart(BaseModel):
    type: Literal["start"] = "start"
    partial: AssistantMessage


class AssistantMessageEventTextStart(BaseModel):
    type: Literal["text_start"] = "text_start"
    content_index: int = Field(alias="contentIndex")
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventTextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    content_index: int = Field(alias="contentIndex")
    delta: str
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventTextEnd(BaseModel):
    type: Literal["text_end"] = "text_end"
    content_index: int = Field(alias="contentIndex")
    content: str
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventThinkingStart(BaseModel):
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int = Field(alias="contentIndex")
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventThinkingDelta(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int = Field(alias="contentIndex")
    delta: str
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventThinkingEnd(BaseModel):
    type: Literal["thinking_end"] = "thinking_end"
    content_index: int = Field(alias="contentIndex")
    content: str
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventToolcallStart(BaseModel):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int = Field(alias="contentIndex")
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventToolcallDelta(BaseModel):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int = Field(alias="contentIndex")
    delta: str
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventToolcallEnd(BaseModel):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int = Field(alias="contentIndex")
    tool_call: ToolCall = Field(alias="toolCall")
    partial: AssistantMessage

    model_config = {"populate_by_name": True}


class AssistantMessageEventDone(BaseModel):
    type: Literal["done"] = "done"
    reason: Literal["stop", "length", "toolUse"]
    message: AssistantMessage


class AssistantMessageEventError(BaseModel):
    type: Literal["error"] = "error"
    reason: Literal["aborted", "error"]
    error: AssistantMessage


AssistantMessageEvent = (
    AssistantMessageEventStart
    | AssistantMessageEventTextStart
    | AssistantMessageEventTextDelta
    | AssistantMessageEventTextEnd
    | AssistantMessageEventThinkingStart
    | AssistantMessageEventThinkingDelta
    | AssistantMessageEventThinkingEnd
    | AssistantMessageEventToolcallStart
    | AssistantMessageEventToolcallDelta
    | AssistantMessageEventToolcallEnd
    | AssistantMessageEventDone
    | AssistantMessageEventError
)
