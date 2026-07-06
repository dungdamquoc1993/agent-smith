"""Unified LLM API for Agent Smith."""

from agent_smith.core.llm.api import complete, complete_simple, stream, stream_simple
from agent_smith.core.llm.events import AssistantMessageEventStream, create_assistant_message_event_stream
from agent_smith.core.llm.models import (
    clear_models,
    get_model,
    get_models,
    get_providers,
    load_models_from_file,
    make_litellm_model,
    register_model,
    register_models,
)
from agent_smith.core.llm.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    HookPayload,
    ImageContent,
    JsonObject,
    JsonPrimitive,
    JsonValue,
    Message,
    MaybeAwaitable,
    Model,
    ProviderPayload,
    SimpleStreamOptions,
    StopReason,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ThinkingLevel,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

__all__ = [
    "AssistantMessage",
    "AssistantMessageEvent",
    "AssistantMessageEventStream",
    "Context",
    "HookPayload",
    "ImageContent",
    "JsonObject",
    "JsonPrimitive",
    "JsonValue",
    "Message",
    "MaybeAwaitable",
    "Model",
    "ProviderPayload",
    "SimpleStreamOptions",
    "StopReason",
    "StreamOptions",
    "TextContent",
    "ThinkingContent",
    "ThinkingLevel",
    "Tool",
    "ToolCall",
    "ToolResultMessage",
    "Usage",
    "UserMessage",
    "complete",
    "complete_simple",
    "create_assistant_message_event_stream",
    "clear_models",
    "get_model",
    "get_models",
    "get_providers",
    "load_models_from_file",
    "make_litellm_model",
    "register_model",
    "register_models",
    "stream",
    "stream_simple",
    "register_litellm_provider",
    "bootstrap_providers",
]


def register_litellm_provider() -> None:
    from agent_smith.infra.llm.litellm_provider import register_litellm_provider as _register

    _register()


def bootstrap_providers() -> None:
    """Register built-in API providers."""
    register_litellm_provider()
