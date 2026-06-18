"""Model catalog and lookup."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Literal

from agent_smith.ai.types import Api, Model, ModelCost, Provider

ModelInput = Literal["text", "image"]

_CATALOG: list[Model] = []
_BY_PROVIDER_ID: dict[tuple[Provider, str], Model] = {}


def _load_models_payload(payload: Any) -> list[Model]:
    raw_models = payload.get("models") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        raise ValueError("Model catalog must be a list or an object with a 'models' list")
    return [Model.model_validate(item) for item in raw_models]


def register_model(model: Model) -> None:
    existing_index = next(
        (idx for idx, current in enumerate(_CATALOG) if (current.provider, current.id) == (model.provider, model.id)),
        None,
    )
    if existing_index is None:
        _CATALOG.append(model)
    else:
        _CATALOG[existing_index] = model
    _BY_PROVIDER_ID[(model.provider, model.id)] = model


def register_models(models: Iterable[Model]) -> None:
    for model in models:
        register_model(model)


def clear_models() -> None:
    _CATALOG.clear()
    _BY_PROVIDER_ID.clear()


def load_models_from_file(path: str | Path, *, replace: bool = False) -> list[Model]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    models = _load_models_payload(payload)
    if replace:
        clear_models()
    register_models(models)
    return models


def _load_builtin_models() -> None:
    data = resources.files(__package__).joinpath("models.catalog.json").read_text(encoding="utf-8")
    register_models(_load_models_payload(json.loads(data)))


def make_litellm_model(
    *,
    provider: Provider,
    model_id: str,
    name: str | None = None,
    litellm_model: str | None = None,
    api: Api = "litellm",
    base_url: str = "",
    reasoning: bool = False,
    input: list[ModelInput] | None = None,
    cost: ModelCost | dict[str, float] | None = None,
    context_window: int = 128_000,
    max_tokens: int = 16_384,
    headers: dict[str, str] | None = None,
    provider_options: dict[str, Any] | None = None,
    compat: dict[str, Any] | None = None,
    thinking_level_map: dict[str, str | None] | None = None,
) -> Model:
    return Model(
        id=model_id,
        name=name or model_id,
        api=api,
        provider=provider,
        base_url=base_url,
        reasoning=reasoning,
        input=input or ["text"],
        cost=cost or ModelCost(),
        context_window=context_window,
        max_tokens=max_tokens,
        headers=headers,
        provider_options=provider_options,
        compat=compat,
        thinking_level_map=thinking_level_map,
        litellm_model=litellm_model,
    )


def get_providers() -> list[Provider]:
    seen: list[Provider] = []
    for model in _CATALOG:
        if model.provider not in seen:
            seen.append(model.provider)
    return seen


def get_models(provider: Provider) -> list[Model]:
    return [m for m in _CATALOG if m.provider == provider]


def get_model(provider: Provider, model_id: str) -> Model | None:
    return _BY_PROVIDER_ID.get((provider, model_id))
_load_builtin_models()
