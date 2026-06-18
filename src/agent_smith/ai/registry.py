"""API provider registry."""

from __future__ import annotations

from typing import Protocol

from agent_smith.ai.events import AssistantMessageEventStream
from agent_smith.ai.types import Api, Context, Model, SimpleStreamOptions, StreamOptions


class ApiProvider(Protocol):
    api: Api

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream: ...

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream: ...


_registry: dict[str, ApiProvider] = {}


def register_api_provider(provider: ApiProvider, source_id: str | None = None) -> None:
    _registry[provider.api] = provider
    _ = source_id  # reserved for test cleanup like pi


def get_api_provider(api: Api) -> ApiProvider | None:
    return _registry.get(api)


def get_api_providers() -> list[ApiProvider]:
    return list(_registry.values())


def clear_api_providers() -> None:
    _registry.clear()


def unregister_api_providers(source_id: str) -> None:
    _ = source_id
    # simplified: tests can call clear_api_providers()
