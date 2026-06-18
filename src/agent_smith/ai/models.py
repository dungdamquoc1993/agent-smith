"""Model catalog and lookup."""

from __future__ import annotations

from agent_smith.ai.types import Model, ModelCost, Provider

_CATALOG: list[Model] = [
    Model(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        api="litellm",
        provider="openai",
        litellm_model="openai/gpt-4o-mini",
        reasoning=False,
        input=["text", "image"],
        cost=ModelCost(input=0.15, output=0.60),
        context_window=128_000,
        max_tokens=16_384,
    ),
    Model(
        id="gpt-4o",
        name="GPT-4o",
        api="litellm",
        provider="openai",
        litellm_model="openai/gpt-4o",
        reasoning=False,
        input=["text", "image"],
        cost=ModelCost(input=2.5, output=10.0),
        context_window=128_000,
        max_tokens=16_384,
    ),
    Model(
        id="claude-3-5-sonnet-20241022",
        name="Claude 3.5 Sonnet",
        api="litellm",
        provider="anthropic",
        litellm_model="anthropic/claude-3-5-sonnet-20241022",
        reasoning=True,
        input=["text", "image"],
        cost=ModelCost(input=3.0, output=15.0),
        context_window=200_000,
        max_tokens=8_192,
    ),
    Model(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        api="litellm",
        provider="google",
        litellm_model="gemini/gemini-2.5-flash",
        reasoning=True,
        input=["text", "image"],
        cost=ModelCost(input=0.15, output=0.60),
        context_window=1_000_000,
        max_tokens=8_192,
    ),
]

_BY_PROVIDER_ID: dict[tuple[Provider, str], Model] = {
    (m.provider, m.id): m for m in _CATALOG
}


def get_providers() -> list[Provider]:
    seen: list[Provider] = []
    for m in _CATALOG:
        if m.provider not in seen:
            seen.append(m.provider)
    return seen


def get_models(provider: Provider) -> list[Model]:
    return [m for m in _CATALOG if m.provider == provider]


def get_model(provider: Provider, model_id: str) -> Model | None:
    return _BY_PROVIDER_ID.get((provider, model_id))
