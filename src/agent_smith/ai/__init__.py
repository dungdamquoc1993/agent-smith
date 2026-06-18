"""Unified LLM API for Agent Smith."""

from agent_smith.ai.api import complete, complete_simple, stream, stream_simple
from agent_smith.ai.events import AssistantMessageEventStream, create_assistant_message_event_stream
from agent_smith.ai.models import get_model, get_models, get_providers
from agent_smith.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    ImageContent,
    Message,
    Model,
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
    "ImageContent",
    "Message",
    "Model",
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
    "get_model",
    "get_models",
    "get_providers",
    "stream",
    "stream_simple",
    "register_litellm_provider",
    "bootstrap_providers",
]


def register_litellm_provider() -> None:
    from agent_smith.ai.providers.litellm_provider import register_litellm_provider as _register

    _register()


def bootstrap_providers() -> None:
    """Register built-in API providers."""
    register_litellm_provider()
