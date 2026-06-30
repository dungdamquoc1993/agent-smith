"""LiteLLM transport adapter."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import litellm

from ai.env_keys import get_google_vertex_config
from ai.events import AssistantMessageEventStream, create_assistant_message_event_stream
from ai.registry import register_api_provider
from ai.types import (
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


def _assistant_text(message: AssistantMessage) -> str:
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent))


def _tool_result_to_litellm_messages(msg: ToolResultMessage) -> list[dict[str, Any]]:
    text = "\n".join(
        b.text for b in msg.content if isinstance(b, TextContent)
    )
    images = [b for b in msg.content if isinstance(b, ImageContent)]

    if not images:
        return [
            {
                "role": "tool",
                "tool_call_id": msg.tool_call_id,
                "name": msg.tool_name,
                "content": text,
            }
        ]

    image_count = len(images)
    tool_text = text or (
        f"Tool returned {image_count} image"
        f"{'' if image_count == 1 else 's'}. The image content follows."
    )
    image_content: list[TextContent | ImageContent] = [
        TextContent(
            text=(
                f"Image content returned by tool {msg.tool_name} "
                f"for tool call {msg.tool_call_id}:"
            )
        ),
        *images,
    ]

    return [
        {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "name": msg.tool_name,
            "content": tool_text,
        },
        {"role": "user", "content": _content_to_litellm(image_content)},
    ]


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
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
        elif isinstance(msg, ToolResultMessage):
            messages.extend(_tool_result_to_litellm_messages(msg))
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


def _resolve_litellm_model(model: Model, opts: StreamOptions | None = None) -> str:
    env = opts.env if opts else None
    has_explicit_api_key = bool(opts and opts.api_key)
    if model.provider == "google" and get_google_vertex_config(env) and not has_explicit_api_key:
        return f"vertex_ai/{model.id}"
    if model.litellm_model:
        return model.litellm_model
    if model.provider == "openai":
        return f"openai/{model.id}"
    if model.provider == "anthropic":
        return f"anthropic/{model.id}"
    if model.provider == "google":
        return f"gemini/{model.id}"
    if model.provider == "openrouter":
        return f"openrouter/{model.id}"
    return model.id


def _apply_provider_auth(model: Model, kwargs: dict[str, Any], opts: StreamOptions) -> None:
    if opts.api_key:
        kwargs["api_key"] = opts.api_key
        return

    if model.provider == "google":
        vertex = get_google_vertex_config(opts.env)
        if vertex:
            kwargs["vertex_project"] = vertex["vertex_project"]
            kwargs["vertex_location"] = vertex["vertex_location"]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _merge_headers(*headers: dict[str, str] | None) -> dict[str, str] | None:
    merged: dict[str, str] = {}
    for value in headers:
        if value:
            merged.update(value)
    return merged or None


def _public_extra_options(opts: StreamOptions) -> dict[str, Any]:
    ignored = {"reasoning", "providerOptions"}
    return {
        key: value
        for key, value in (opts.model_extra or {}).items()
        if value is not None and key not in ignored
    }


def _build_litellm_kwargs(model: Model, context: Context, opts: StreamOptions) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": _resolve_litellm_model(model, opts),
        "messages": _context_to_litellm_messages(context),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if model.provider_options:
        kwargs.update(model.provider_options)
    if opts.provider_options:
        kwargs.update(opts.provider_options)
    kwargs.update(_public_extra_options(opts))

    if model.base_url:
        kwargs["api_base"] = model.base_url
    if context.tools:
        kwargs["tools"] = _tools_to_litellm(context.tools)
    if opts.temperature is not None:
        kwargs["temperature"] = opts.temperature
    if opts.max_tokens is not None:
        kwargs["max_tokens"] = opts.max_tokens
    if opts.timeout_ms:
        kwargs["timeout"] = opts.timeout_ms / 1000
    if opts.max_retries is not None:
        kwargs["num_retries"] = opts.max_retries
    if opts.max_retry_delay_ms is not None:
        kwargs["max_retry_delay"] = opts.max_retry_delay_ms / 1000
    if opts.metadata:
        kwargs["metadata"] = opts.metadata
    if opts.cache_retention:
        kwargs["cache_retention"] = opts.cache_retention

    headers = _merge_headers(model.headers, opts.headers)
    if headers:
        kwargs["headers"] = headers

    _apply_provider_auth(model, kwargs, opts)
    return kwargs


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


def _map_reasoning(model: Model, reasoning: str | None) -> str | None:
    if reasoning is None:
        return None
    if model.thinking_level_map and reasoning in model.thinking_level_map:
        return model.thinking_level_map[reasoning]
    if reasoning == "xhigh":
        return "high"
    return reasoning


def _provider_option(model: Model, opts: StreamOptions, name: str, default: Any = None) -> Any:
    if opts.provider_options and name in opts.provider_options:
        return opts.provider_options[name]
    if model.provider_options and name in model.provider_options:
        return model.provider_options[name]
    return default


def _use_ollama_native(model: Model, opts: StreamOptions) -> bool:
    return bool(_provider_option(model, opts, "ollama_native", False))


def _ollama_model_name(model: Model) -> str:
    litellm_model = model.resolve_litellm_model()
    if "/" in litellm_model:
        return litellm_model.split("/", 1)[1]
    return litellm_model


def _ollama_base_url(model: Model) -> str:
    base = (model.base_url or "http://localhost:11434").rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def _content_to_ollama(content: str | list[TextContent | ImageContent]) -> dict[str, Any]:
    if isinstance(content, str):
        return {"content": content}
    text_parts: list[str] = []
    images: list[str] = []
    for part in content:
        if isinstance(part, TextContent):
            text_parts.append(part.text)
        elif isinstance(part, ImageContent):
            images.append(part.data)
    payload: dict[str, Any] = {"content": "\n".join(text_parts)}
    if images:
        payload["images"] = images
    return payload


def _context_to_ollama_messages(context: Context) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if context.system_prompt:
        messages.append({"role": "system", "content": context.system_prompt})
    for msg in context.messages:
        if isinstance(msg, UserMessage):
            messages.append({"role": "user", **_content_to_ollama(msg.content)})
        elif isinstance(msg, AssistantMessage):
            messages.append({"role": "assistant", "content": _assistant_text(msg)})
        elif isinstance(msg, ToolResultMessage):
            text = "\n".join(
                block.text if isinstance(block, TextContent) else f"[image: {block.mime_type}]"
                for block in msg.content
            )
            messages.append({"role": "tool", "content": text})
    return messages


async def _ollama_chat_stream(
    *,
    base_url: str,
    payload: dict[str, Any],
    timeout: float | None,
) -> Any:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported Ollama URL scheme: {parsed.scheme}")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ssl = parsed.scheme == "https"
    path_prefix = parsed.path.rstrip("/")
    path = f"{path_prefix}/api/chat" if path_prefix else "/api/chat"
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ssl),
        timeout=timeout,
    )
    try:
        writer.write(
            b"\r\n".join(
                [
                    f"POST {path} HTTP/1.1".encode(),
                    f"Host: {host}".encode(),
                    b"Content-Type: application/json",
                    f"Content-Length: {len(body)}".encode(),
                    b"Connection: close",
                    b"",
                    body,
                ]
            )
        )
        await writer.drain()

        status_line = await reader.readline()
        if not status_line:
            raise RuntimeError("Ollama closed connection before responding")
        try:
            status = int(status_line.decode("iso-8859-1").split()[1])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"Invalid Ollama response: {status_line!r}") from exc

        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in {b"\r\n", b"\n", b""}:
                break
            name, _, value = line.decode("iso-8859-1").partition(":")
            headers[name.strip().lower()] = value.strip().lower()

        async def read_body() -> bytes:
            if headers.get("transfer-encoding") == "chunked":
                chunks: list[bytes] = []
                while True:
                    size_line = await reader.readline()
                    if not size_line:
                        break
                    size = int(size_line.strip().split(b";", 1)[0], 16)
                    if size == 0:
                        break
                    chunks.append(await reader.readexactly(size))
                    await reader.readexactly(2)
                return b"".join(chunks)
            return await reader.read()

        if status >= 400:
            body_text = (await read_body()).decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama API error {status}: {body_text}")

        if headers.get("transfer-encoding") == "chunked":
            while True:
                size_line = await reader.readline()
                if not size_line:
                    break
                size = int(size_line.strip().split(b";", 1)[0], 16)
                if size == 0:
                    break
                chunk = await reader.readexactly(size)
                await reader.readexactly(2)
                for raw in chunk.splitlines():
                    if raw.strip():
                        yield json.loads(raw)
        else:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                if raw.strip():
                    yield json.loads(raw)
    finally:
        writer.close()
        await writer.wait_closed()


def _usage_token_count(usage: Any, *names: str) -> int:
    for name in names:
        value = _get(usage, name)
        if isinstance(value, int | float):
            return int(value)
    return 0


def _usage_detail_token_count(usage: Any, detail_name: str, *names: str) -> int:
    details = _get(usage, detail_name)
    if details is None:
        return 0
    return _usage_token_count(details, *names)


def _usage_from_litellm(usage: Any) -> Usage:
    cache_read = _usage_token_count(
        usage,
        "cache_read_input_tokens",
        "prompt_cache_hit_tokens",
        "cache_read_tokens",
    ) or _usage_detail_token_count(usage, "prompt_tokens_details", "cached_tokens")
    cache_write = _usage_token_count(
        usage,
        "cache_creation_input_tokens",
        "cache_write_tokens",
        "prompt_cache_miss_tokens",
    )
    cache_write_1h = _usage_token_count(usage, "cache_creation_input_tokens_1h")
    return Usage(
        input=_usage_token_count(usage, "prompt_tokens", "input_tokens"),
        output=_usage_token_count(usage, "completion_tokens", "output_tokens"),
        cache_read=cache_read,
        cache_write=cache_write,
        cache_write_1h=cache_write_1h or None,
        total_tokens=_usage_token_count(usage, "total_tokens"),
    )


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
        reasoning = _map_reasoning(model, opts.reasoning)
        extra = opts.model_dump(exclude_none=True, by_alias=True)
        if reasoning and model.reasoning:
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
            if _use_ollama_native(model, opts):
                await self._run_ollama_native_stream(model, context, opts, event_stream, partial)
                return

            kwargs = _build_litellm_kwargs(model, context, opts)

            response = await litellm.acompletion(**kwargs)

            text_index: int | None = None
            thinking_index: int | None = None
            thinking_closed = False
            tool_base_index: int | None = None
            tool_states: dict[int, dict[str, Any]] = {}
            accumulated_text = ""
            accumulated_thinking = ""
            final_usage = Usage()
            stop_reason: str = "stop"
            response_model: str | None = None
            response_id: str | None = _get(response, "id")

            async for chunk in response:
                if _get(chunk, "model"):
                    response_model = _get(chunk, "model")
                if _get(chunk, "id"):
                    response_id = _get(chunk, "id")

                choices = _get(chunk, "choices") or []
                choice = choices[0] if choices else None
                delta = _get(choice, "delta") if choice else None
                finish = _get(choice, "finish_reason") if choice else None

                if finish == "tool_calls":
                    stop_reason = "toolUse"
                elif finish == "length":
                    stop_reason = "length"
                elif finish == "stop":
                    stop_reason = "stop"

                if delta:
                    # Reasoning / thinking content (provider-dependent)
                    reasoning_content = _get(delta, "reasoning_content") or _get(delta, "thinking")
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

                    delta_content = _get(delta, "content")
                    if delta_content:
                        if text_index is None:
                            if thinking_index is not None and not thinking_closed:
                                event_stream.push(
                                    AssistantMessageEventThinkingEnd(
                                        content_index=thinking_index,
                                        content=accumulated_thinking,
                                        partial=partial.model_copy(deep=True),
                                    )
                                )
                                thinking_closed = True
                            text_index = len(partial.content)
                            partial.content.append(TextContent(text=""))
                            event_stream.push(
                                AssistantMessageEventTextStart(
                                    content_index=text_index,
                                    partial=partial.model_copy(deep=True),
                                )
                            )
                        accumulated_text += delta_content
                        partial.content[text_index] = TextContent(text=accumulated_text)
                        event_stream.push(
                            AssistantMessageEventTextDelta(
                                content_index=text_index,
                                delta=delta_content,
                                partial=partial.model_copy(deep=True),
                            )
                        )

                    tool_calls = _get(delta, "tool_calls") or []
                    if tool_calls:
                        if tool_base_index is None:
                            tool_base_index = text_index + 1 if text_index is not None else len(partial.content)
                        for tc in tool_calls:
                            idx = _get(tc, "index", 0)
                            state = tool_states.setdefault(
                                idx,
                                {"id": "", "name": "", "arguments": "", "started": False},
                            )
                            function = _get(tc, "function")
                            if _get(tc, "id"):
                                state["id"] = _get(tc, "id")
                            if function and _get(function, "name"):
                                state["name"] = _get(function, "name")
                            tool_content_index = tool_base_index + idx
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
                            arguments_delta = _get(function, "arguments") if function else None
                            if arguments_delta:
                                state["arguments"] += arguments_delta
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
                                        delta=arguments_delta,
                                        partial=partial.model_copy(deep=True),
                                    )
                                )

                usage = _get(chunk, "usage")
                if usage:
                    final_usage = _usage_from_litellm(usage)

            # Finalize text block
            if text_index is not None:
                event_stream.push(
                    AssistantMessageEventTextEnd(
                        content_index=text_index,
                        content=accumulated_text,
                        partial=partial.model_copy(deep=True),
                    )
                )
            if thinking_index is not None and accumulated_thinking and not thinking_closed:
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
                base_index = tool_base_index
                if base_index is None:
                    base_index = text_index + 1 if text_index is not None else len(partial.content)
                tool_content_index = base_index + idx
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
            partial.response_id = response_id
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

    async def _run_ollama_native_stream(
        self,
        model: Model,
        context: Context,
        opts: StreamOptions,
        event_stream: AssistantMessageEventStream,
        partial: AssistantMessage,
    ) -> None:
        thinking_setting = _provider_option(model, opts, "ollama_think")
        if thinking_setting is None:
            thinking_setting = bool(model.reasoning and (opts.model_extra or {}).get("reasoning"))
        payload: dict[str, Any] = {
            "model": _ollama_model_name(model),
            "messages": _context_to_ollama_messages(context),
            "stream": True,
            "think": thinking_setting,
        }
        if context.tools:
            payload["tools"] = _tools_to_litellm(context.tools)
        options: dict[str, Any] = {}
        if opts.temperature is not None:
            options["temperature"] = opts.temperature
        if opts.max_tokens is not None:
            options["num_predict"] = opts.max_tokens
        if options:
            payload["options"] = options

        text_index: int | None = None
        thinking_index: int | None = None
        thinking_closed = False
        accumulated_text = ""
        accumulated_thinking = ""
        response_model: str | None = None
        final_chunk: dict[str, Any] = {}
        stop_reason: str = "stop"

        async for chunk in _ollama_chat_stream(
            base_url=_ollama_base_url(model),
            payload=payload,
            timeout=opts.timeout_ms / 1000 if opts.timeout_ms else None,
        ):
            final_chunk = chunk
            response_model = chunk.get("model") or response_model
            message = chunk.get("message") or {}

            reasoning_content = message.get("thinking") or message.get("reasoning_content")
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

            delta_content = message.get("content")
            if delta_content:
                if text_index is None:
                    if thinking_index is not None and not thinking_closed:
                        event_stream.push(
                            AssistantMessageEventThinkingEnd(
                                content_index=thinking_index,
                                content=accumulated_thinking,
                                partial=partial.model_copy(deep=True),
                            )
                        )
                        thinking_closed = True
                    text_index = len(partial.content)
                    partial.content.append(TextContent(text=""))
                    event_stream.push(
                        AssistantMessageEventTextStart(
                            content_index=text_index,
                            partial=partial.model_copy(deep=True),
                        )
                    )
                accumulated_text += delta_content
                partial.content[text_index] = TextContent(text=accumulated_text)
                event_stream.push(
                    AssistantMessageEventTextDelta(
                        content_index=text_index,
                        delta=delta_content,
                        partial=partial.model_copy(deep=True),
                    )
                )

            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                stop_reason = "toolUse"
                for idx, raw_call in enumerate(tool_calls):
                    function = raw_call.get("function") or {}
                    arguments = function.get("arguments") or {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                    tool_call = ToolCall(
                        id=raw_call.get("id") or str(uuid.uuid4()),
                        name=function.get("name") or raw_call.get("name") or "",
                        arguments=arguments,
                    )
                    content_index = len(partial.content) + idx
                    event_stream.push(
                        AssistantMessageEventToolcallStart(
                            content_index=content_index,
                            partial=partial.model_copy(deep=True),
                        )
                    )
                    partial.content.append(tool_call)
                    event_stream.push(
                        AssistantMessageEventToolcallEnd(
                            content_index=content_index,
                            tool_call=tool_call,
                            partial=partial.model_copy(deep=True),
                        )
                    )

            if chunk.get("done"):
                break

        if text_index is not None:
            event_stream.push(
                AssistantMessageEventTextEnd(
                    content_index=text_index,
                    content=accumulated_text,
                    partial=partial.model_copy(deep=True),
                )
            )
        if thinking_index is not None and accumulated_thinking and not thinking_closed:
            event_stream.push(
                AssistantMessageEventThinkingEnd(
                    content_index=thinking_index,
                    content=accumulated_thinking,
                    partial=partial.model_copy(deep=True),
                )
            )

        prompt_tokens = int(final_chunk.get("prompt_eval_count") or 0)
        output_tokens = int(final_chunk.get("eval_count") or 0)
        partial.usage = Usage(
            input=prompt_tokens,
            output=output_tokens,
            totalTokens=prompt_tokens + output_tokens,
        )
        partial.usage.cost = _compute_cost(model, partial.usage)
        partial.response_model = response_model
        partial.stop_reason = stop_reason  # type: ignore[assignment]
        event_stream.push(
            AssistantMessageEventDone(
                reason=stop_reason,  # type: ignore[arg-type]
                message=partial.model_copy(deep=True),
            )
        )


def register_litellm_provider() -> None:
    register_api_provider(LitellmApiProvider())
