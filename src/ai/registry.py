"""API provider registry."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class _RegisteredApiProvider:
    provider: ApiProvider
    source_id: str | None = None

    @property
    def api(self) -> Api:
        return self.provider.api

    def _validate_model(self, model: Model) -> None:
        if model.api != self.provider.api:
            raise ValueError(f"Mismatched api: {model.api} expected {self.provider.api}")

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        self._validate_model(model)
        return self.provider.stream(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        self._validate_model(model)
        return self.provider.stream_simple(model, context, options)


_registry: dict[str, _RegisteredApiProvider] = {}


def register_api_provider(provider: ApiProvider, source_id: str | None = None) -> None:
    _registry[provider.api] = _RegisteredApiProvider(provider=provider, source_id=source_id)


def get_api_provider(api: Api) -> ApiProvider | None:
    return _registry.get(api)


def get_api_providers() -> list[ApiProvider]:
    return list(_registry.values())


def clear_api_providers() -> None:
    _registry.clear()


def unregister_api_providers(source_id: str) -> None:
    for api, entry in list(_registry.items()):
        if entry.source_id == source_id:
            del _registry[api]
