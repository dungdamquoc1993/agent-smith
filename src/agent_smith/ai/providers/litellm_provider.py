"""LiteLLM transport adapter."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import litellm

from agent_smith.ai.env_keys import get_env_api_key, get_google_vertex_config
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
    AssistantMessageEventToolcallDelta,
    AssistantMessageEventToolcallEnd,
    AssistantMessageEventToolcallStart,
    Context,
    ImageContent,
    Model,
    SimpleStreamOptions,
    StreamOptions,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)


def _content_to_litellm(content: str | list[TextContent | ImageContent]) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for block in content:
        if block.type == "text":
            parts.append({"type": "text", "text": block.text})
        elif block.type == "image":
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                }
            )
    return parts


def _context_to_litellm_messages(context: Context) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if context.system_prompt:
        messages.append({"role": "system", "content": context.system_prompt})

    for msg in context.messages:
        if isinstance(msg, UserMessage):
            messages.append({"role": "user", "content": _content_to_litellm(msg.content)})
        elif msg.role == "assistant":
            # Reconstruct assistant message for litellm
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in msg.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "thinking":
                    text_parts.append(f"<thinking>{block.thinking}</thinking>")
                elif block.type == "toolCall":
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": json.dumps(block.arguments),
                            },
                        }
                    )
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
        elif isinstance(msg, ToolResultMessage):
            text = "\n".join(
                b.text for b in msg.content if isinstance(b, TextContent)
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "name": msg.tool_name,
                    "content": text,
                }
            )
    return messages


def _tools_to_litellm(tools: list[Tool] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def _resolve_litellm_model(model: Model) -> str:
    if model.provider == "google" and get_google_vertex_config() and not get_env_api_key(model.provider):
        return f"vertex_ai/{model.id}"
    return model.resolve_litellm_model()


def _apply_provider_auth(model: Model, kwargs: dict[str, Any], opts: StreamOptions) -> None:
    if opts.api_key:
        kwargs["api_key"] = opts.api_key
        return

    if model.provider == "google":
        vertex = get_google_vertex_config()
        if vertex:
            kwargs["vertex_project"] = vertex["vertex_project"]
            kwargs["vertex_location"] = vertex["vertex_location"]


def _empty_assistant(model: Model) -> AssistantMessage:
    return AssistantMessage(
        api=model.api,
        provider=model.provider,
        model=model.id,
        timestamp=int(time.time() * 1000),
    )


def _compute_cost(model: Model, usage: Usage) -> UsageCost:
    cost = UsageCost(
        input=usage.input * model.cost.input / 1_000_000,
        output=usage.output * model.cost.output / 1_000_000,
        cache_read=usage.cache_read * model.cost.cache_read / 1_000_000,
        cache_write=usage.cache_write * model.cost.cache_write / 1_000_000,
    )
    cost.total = cost.input + cost.output + cost.cache_read + cost.cache_write
    return cost


def _map_reasoning(reasoning: str | None) -> str | None:
    if reasoning is None:
        return None
    if reasoning == "xhigh":
        return "high"
    return reasoning


class LitellmApiProvider:
    api = "litellm"

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        stream = create_assistant_message_event_stream()
        stream.set_producer(self._run_stream(model, context, options, stream))
        return stream

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        opts = options or SimpleStreamOptions()
        reasoning = _map_reasoning(opts.reasoning)
        extra = opts.model_dump(exclude_none=True, by_alias=True)
        if reasoning:
            extra["reasoning_effort"] = reasoning
        merged = StreamOptions.model_validate(extra)
        return self.stream(model, context, merged)

    async def _run_stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None,
        event_stream: AssistantMessageEventStream,
    ) -> None:
        opts = options or StreamOptions()
        partial = _empty_assistant(model)
        event_stream.push(AssistantMessageEventStart(partial=partial))

        try:
            kwargs: dict[str, Any] = {
                "model": _resolve_litellm_model(model),
                "messages": _context_to_litellm_messages(context),
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if context.tools:
                kwargs["tools"] = _tools_to_litellm(context.tools)
            if opts.temperature is not None:
                kwargs["temperature"] = opts.temperature
            if opts.max_tokens is not None:
                kwargs["max_tokens"] = opts.max_tokens
            _apply_provider_auth(model, kwargs, opts)
            if opts.timeout_ms:
                kwargs["timeout"] = opts.timeout_ms / 1000
            if opts.max_retries is not None:
                kwargs["num_retries"] = opts.max_retries
            if getattr(opts, "reasoning_effort", None):
                kwargs["reasoning_effort"] = opts.reasoning_effort

            response = await litellm.acompletion(**kwargs)

            text_index: int | None = None
            thinking_index: int | None = None
            tool_states: dict[int, dict[str, Any]] = {}
            accumulated_text = ""
            accumulated_thinking = ""
            final_usage = Usage()
            stop_reason: str = "stop"
            response_model: str | None = None

            async for chunk in response:
                if hasattr(chunk, "model") and chunk.model:
                    response_model = chunk.model

                delta = chunk.choices[0].delta if chunk.choices else None
                finish = chunk.choices[0].finish_reason if chunk.choices else None

                if finish == "tool_calls":
                    stop_reason = "toolUse"
                elif finish == "length":
                    stop_reason = "length"
                elif finish == "stop":
                    stop_reason = "stop"

                if delta:
                    # Reasoning / thinking content (provider-dependent)
                    reasoning_content = getattr(delta, "reasoning_content", None) or getattr(
                        delta, "thinking", None
                    )
                    if reasoning_content:
                        if thinking_index is None:
                            thinking_index = len(partial.content)
                            partial.content.append(ThinkingContent(thinking=""))
                            event_stream.push(
                                AssistantMessageEventThinkingStart(
                                    content_index=thinking_index,
                                    partial=partial.model_copy(deep=True),
                                )
                            )
                        accumulated_thinking += reasoning_content
                        partial.content[thinking_index] = ThinkingContent(thinking=accumulated_thinking)
                        event_stream.push(
                            AssistantMessageEventThinkingDelta(
                                content_index=thinking_index,
                                delta=reasoning_content,
                                partial=partial.model_copy(deep=True),
                            )
                        )

                    if delta.content:
                        if text_index is None:
                            if thinking_index is not None:
                                event_stream.push(
                                    AssistantMessageEventThinkingEnd(
                                        content_index=thinking_index,
                                        content=accumulated_thinking,
                                        partial=partial.model_copy(deep=True),
                                    )
                                )
                            text_index = len(partial.content)
                            partial.content.append(TextContent(text=""))
                            event_stream.push(
                                AssistantMessageEventTextStart(
                                    content_index=text_index,
                                    partial=partial.model_copy(deep=True),
                                )
                            )
                        accumulated_text += delta.content
                        partial.content[text_index] = TextContent(text=accumulated_text)
                        event_stream.push(
                            AssistantMessageEventTextDelta(
                                content_index=text_index,
                                delta=delta.content,
                                partial=partial.model_copy(deep=True),
                            )
                        )

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            state = tool_states.setdefault(
                                idx,
                                {"id": "", "name": "", "arguments": "", "started": False},
                            )
                            if tc.id:
                                state["id"] = tc.id
                            if tc.function and tc.function.name:
                                state["name"] = tc.function.name
                            tool_content_index = (text_index or -1) + 1 + idx
                            if not state["started"]:
                                state["started"] = True
                                while len(partial.content) <= tool_content_index:
                                    partial.content.append(
                                        ToolCall(id=state["id"] or str(uuid.uuid4()), name="", arguments={})
                                    )
                                event_stream.push(
                                    AssistantMessageEventToolcallStart(
                                        content_index=tool_content_index,
                                        partial=partial.model_copy(deep=True),
                                    )
                                )
                            if tc.function and tc.function.arguments:
                                state["arguments"] += tc.function.arguments
                                try:
                                    parsed_args = json.loads(state["arguments"]) if state["arguments"] else {}
                                except json.JSONDecodeError:
                                    parsed_args = {}
                                tool_call = ToolCall(
                                    id=state["id"] or str(uuid.uuid4()),
                                    name=state["name"],
                                    arguments=parsed_args,
                                )
                                partial.content[tool_content_index] = tool_call
                                event_stream.push(
                                    AssistantMessageEventToolcallDelta(
                                        content_index=tool_content_index,
                                        delta=tc.function.arguments or "",
                                        partial=partial.model_copy(deep=True),
                                    )
                                )

                if hasattr(chunk, "usage") and chunk.usage:
                    u = chunk.usage
                    final_usage = Usage(
                        input=getattr(u, "prompt_tokens", 0) or 0,
                        output=getattr(u, "completion_tokens", 0) or 0,
                        total_tokens=getattr(u, "total_tokens", 0) or 0,
                    )

            # Finalize text block
            if text_index is not None:
                event_stream.push(
                    AssistantMessageEventTextEnd(
                        content_index=text_index,
                        content=accumulated_text,
                        partial=partial.model_copy(deep=True),
                    )
                )
            if thinking_index is not None and accumulated_thinking:
                event_stream.push(
                    AssistantMessageEventThinkingEnd(
                        content_index=thinking_index,
                        content=accumulated_thinking,
                        partial=partial.model_copy(deep=True),
                    )
                )

            # Finalize tool calls
            has_tools = False
            for idx, state in sorted(tool_states.items()):
                has_tools = True
                tool_content_index = (text_index + 1 + idx) if text_index is not None else idx
                try:
                    parsed_args = json.loads(state["arguments"]) if state["arguments"] else {}
                except json.JSONDecodeError:
                    parsed_args = {}
                tool_call = ToolCall(
                    id=state["id"] or str(uuid.uuid4()),
                    name=state["name"],
                    arguments=parsed_args,
                )
                while len(partial.content) <= tool_content_index:
                    partial.content.append(tool_call)
                partial.content[tool_content_index] = tool_call
                event_stream.push(
                    AssistantMessageEventToolcallEnd(
                        content_index=tool_content_index,
                        tool_call=tool_call,
                        partial=partial.model_copy(deep=True),
                    )
                )

            if has_tools:
                stop_reason = "toolUse"

            final_usage.cost = _compute_cost(model, final_usage)
            partial.usage = final_usage
            partial.response_model = response_model
            partial.stop_reason = stop_reason  # type: ignore[assignment]

            reason = stop_reason if stop_reason in ("stop", "length", "toolUse") else "stop"
            event_stream.push(
                AssistantMessageEventDone(
                    reason=reason,  # type: ignore[arg-type]
                    message=partial.model_copy(deep=True),
                )
            )
        except Exception as exc:
            err_msg = _empty_assistant(model)
            err_msg.stop_reason = "error"
            err_msg.error_message = str(exc)
            event_stream.push(
                AssistantMessageEventError(reason="error", error=err_msg)
            )


def register_litellm_provider() -> None:
    register_api_provider(LitellmApiProvider())
