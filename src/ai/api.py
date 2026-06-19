"""Public API: stream, complete, stream_simple, complete_simple."""

from __future__ import annotations

from ai.env_keys import get_env_api_key, get_google_vertex_config
from ai.events import AssistantMessageEventStream
from ai.registry import get_api_provider
from ai.types import (
    Api,
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    StreamOptions,
)


def _has_explicit_api_key(api_key: str | None) -> bool:
    return isinstance(api_key, str) and bool(api_key.strip())


def _with_env_api_key(model: Model, options: StreamOptions | None) -> StreamOptions | None:
    if options and _has_explicit_api_key(options.api_key):
        return options
    env = options.env if options else None
    if model.provider == "google" and get_google_vertex_config(env):
        return options
    api_key = get_env_api_key(model.provider, env)
    if not api_key:
        return options
    if options is None:
        return StreamOptions(api_key=api_key)
    return options.model_copy(update={"api_key": api_key})


def _resolve_api_provider(api: Api):
    provider = get_api_provider(api)
    if provider is None:
        raise ValueError(f"No API provider registered for api: {api}")
    return provider


def stream(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessageEventStream:
    provider = _resolve_api_provider(model.api)
    return provider.stream(model, context, _with_env_api_key(model, options))


async def complete(
    model: Model,
    context: Context,
    options: StreamOptions | None = None,
) -> AssistantMessage:
    s = stream(model, context, options)
    return await s.result()


def stream_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    provider = _resolve_api_provider(model.api)
    return provider.stream_simple(model, context, _with_env_api_key(model, options))


async def complete_simple(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessage:
    s = stream_simple(model, context, options)
    return await s.result()
