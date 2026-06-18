"""Faux/offline provider for deterministic tests."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent_smith.ai.events import AssistantMessageEventStream, create_assistant_message_event_stream
from agent_smith.ai.registry import register_api_provider
from agent_smith.ai.types import (
    AssistantMessage,
    AssistantMessageEventDone,
    AssistantMessageEventError,
    AssistantMessageEventStart,
    AssistantMessageEventTextDelta,
    AssistantMessageEventTextEnd,
    AssistantMessageEventTextStart,
    AssistantMessageEventThinkingDelta,
    AssistantMessageEventThinkingEnd,
    AssistantMessageEventThinkingStart,
    AssistantMessageEventToolcallEnd,
    AssistantMessageEventToolcallStart,
    Context,
    Model,
    SimpleStreamOptions,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
    UsageCost,
)


@dataclass
class FauxResponse:
    content: list[Any] = field(default_factory=list)
    stop_reason: str = "stop"


# Global queue for faux responses (per-registration pattern simplified)
_faux_response_queue: list[FauxResponse] = []


def set_faux_responses(responses: list[FauxResponse]) -> None:
    global _faux_response_queue
    _faux_response_queue = list(responses)


def append_faux_responses(responses: list[FauxResponse]) -> None:
    _faux_response_queue.extend(responses)


def clear_faux_responses() -> None:
    _faux_response_queue.clear()


def faux_text(text: str) -> TextContent:
    return TextContent(text=text)


def faux_thinking(thinking: str) -> ThinkingContent:
    return ThinkingContent(thinking=thinking)


def faux_tool_call(name: str, arguments: dict[str, Any]) -> ToolCall:
    return ToolCall(id=f"tool_{uuid.uuid4().hex[:8]}", name=name, arguments=arguments)


def faux_response(
    blocks: list[Any],
    *,
    stop_reason: str = "stop",
) -> FauxResponse:
    return FauxResponse(content=blocks, stop_reason=stop_reason)


def _empty_assistant(model: Model) -> AssistantMessage:
    return AssistantMessage(
        api=model.api,
        provider=model.provider,
        model=model.id,
        timestamp=int(time.time() * 1000),
    )


def _estimate_usage(text: str) -> Usage:
    tokens = max(1, len(text) // 4)
    return Usage(
        input=10,
        output=tokens,
        total_tokens=10 + tokens,
        cost=UsageCost(total=0.0),
    )


class FauxApiProvider:
    api = "faux"

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        stream = create_assistant_message_event_stream()
        stream.set_producer(self._run_stream(model, context, stream))
        return stream

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        return self.stream(model, context, options)

    async def _run_stream(
        self,
        model: Model,
        context: Context,
        event_stream: AssistantMessageEventStream,
    ) -> None:
        partial = _empty_assistant(model)
        event_stream.push(AssistantMessageEventStart(partial=partial))

        try:
            if not _faux_response_queue:
                err = _empty_assistant(model)
                err.stop_reason = "error"
                err.error_message = "No more faux responses queued"
                event_stream.push(AssistantMessageEventError(reason="error", error=err))
                return

            response = _faux_response_queue.pop(0)
            char_budget = 0

            for block in response.content:
                if isinstance(block, ThinkingContent):
                    idx = len(partial.content)
                    partial.content.append(ThinkingContent(thinking=""))
                    event_stream.push(
                        AssistantMessageEventThinkingStart(content_index=idx, partial=partial.model_copy(deep=True))
                    )
                    accumulated = ""
                    for ch in block.thinking:
                        accumulated += ch
                        partial.content[idx] = ThinkingContent(thinking=accumulated)
                        event_stream.push(
                            AssistantMessageEventThinkingDelta(
                                content_index=idx,
                                delta=ch,
                                partial=partial.model_copy(deep=True),
                            )
                        )
                        char_budget += 1
                        if char_budget % 8 == 0:
                            await asyncio.sleep(0)
                    event_stream.push(
                        AssistantMessageEventThinkingEnd(
                            content_index=idx,
                            content=accumulated,
                            partial=partial.model_copy(deep=True),
                        )
                    )

                elif isinstance(block, TextContent):
                    idx = len(partial.content)
                    partial.content.append(TextContent(text=""))
                    event_stream.push(
                        AssistantMessageEventTextStart(content_index=idx, partial=partial.model_copy(deep=True))
                    )
                    accumulated = ""
                    for ch in block.text:
                        accumulated += ch
                        partial.content[idx] = TextContent(text=accumulated)
                        event_stream.push(
                            AssistantMessageEventTextDelta(
                                content_index=idx,
                                delta=ch,
                                partial=partial.model_copy(deep=True),
                            )
                        )
                        char_budget += 1
                        if char_budget % 8 == 0:
                            await asyncio.sleep(0)
                    event_stream.push(
                        AssistantMessageEventTextEnd(
                            content_index=idx,
                            content=accumulated,
                            partial=partial.model_copy(deep=True),
                        )
                    )

                elif isinstance(block, ToolCall):
                    idx = len(partial.content)
                    partial.content.append(block)
                    event_stream.push(
                        AssistantMessageEventToolcallStart(content_index=idx, partial=partial.model_copy(deep=True))
                    )
                    event_stream.push(
                        AssistantMessageEventToolcallEnd(
                            content_index=idx,
                            tool_call=block,
                            partial=partial.model_copy(deep=True),
                        )
                    )

            all_text = "".join(
                b.text for b in partial.content if isinstance(b, TextContent)
            ) + "".join(
                b.thinking for b in partial.content if isinstance(b, ThinkingContent)
            )
            partial.usage = _estimate_usage(all_text)
            reason = response.stop_reason
            if reason not in ("stop", "length", "toolUse"):
                reason = "stop"
            partial.stop_reason = reason  # type: ignore[assignment]
            event_stream.push(
                AssistantMessageEventDone(
                    reason=reason,  # type: ignore[arg-type]
                    message=partial.model_copy(deep=True),
                )
            )
        except Exception as exc:
            err = _empty_assistant(model)
            err.stop_reason = "error"
            err.error_message = str(exc)
            event_stream.push(AssistantMessageEventError(reason="error", error=err))


def register_faux_provider() -> None:
    register_api_provider(FauxApiProvider())
