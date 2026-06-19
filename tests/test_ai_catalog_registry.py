"""Unit tests for AI model and provider registries."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ai import (
    Context,
    Model,
    UserMessage,
    clear_models,
    get_model,
    get_models,
    get_providers,
    load_models_from_file,
    make_litellm_model,
    register_model,
    register_models,
)
from ai.events import create_assistant_message_event_stream
from ai.registry import (
    clear_api_providers,
    get_api_provider,
    get_api_providers,
    register_api_provider,
    unregister_api_providers,
)
from ai.types import StreamOptions


@pytest.fixture()
def restore_models():
    snapshot = [model.model_copy(deep=True) for provider in get_providers() for model in get_models(provider)]
    yield
    clear_models()
    register_models(snapshot)


@pytest.fixture()
def restore_api_providers():
    yield
    clear_api_providers()
    from ai import bootstrap_providers

    bootstrap_providers()


def _context() -> Context:
    return Context(
        messages=[
            UserMessage(role="user", content="hello", timestamp=int(time.time() * 1000)),
        ],
    )


def test_builtin_catalog_preserves_existing_lookup() -> None:
    openai_model = get_model("openai", "gpt-4o-mini")
    google_model = get_model("google", "gemini-2.5-flash")

    assert openai_model is not None
    assert google_model is not None
    assert openai_model.api == google_model.api == "litellm"
    assert get_providers()[:3] == ["openai", "anthropic", "google"]


def test_register_model_and_load_models_from_file(tmp_path: Path, restore_models) -> None:
    custom = make_litellm_model(
        provider="custom-provider",
        model_id="custom-model",
        litellm_model="custom/custom-model",
        provider_options={"api_base": "https://example.test"},
    )

    register_model(custom)
    assert get_model("custom-provider", "custom-model") == custom

    catalog_path = tmp_path / "models.json"
    catalog_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "id": "loaded-model",
                        "name": "Loaded Model",
                        "api": "litellm",
                        "provider": "loaded-provider",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = load_models_from_file(catalog_path)
    assert loaded[0].id == "loaded-model"
    assert get_model("loaded-provider", "loaded-model") == loaded[0]


def test_load_models_from_file_rejects_invalid_shape(tmp_path: Path, restore_models) -> None:
    catalog_path = tmp_path / "models.json"
    catalog_path.write_text(json.dumps({"models": {"not": "a list"}}), encoding="utf-8")

    with pytest.raises(ValueError, match="Model catalog"):
        load_models_from_file(catalog_path)


class _FakeProvider:
    def __init__(self, api: str) -> None:
        self.api = api

    def stream(self, model: Model, context: Context, options: StreamOptions | None = None):
        _ = (model, context, options)
        return create_assistant_message_event_stream()

    def stream_simple(self, model: Model, context: Context, options=None):
        _ = (model, context, options)
        return create_assistant_message_event_stream()


def test_registry_unregisters_by_source_id(restore_api_providers) -> None:
    clear_api_providers()
    register_api_provider(_FakeProvider("one"), source_id="a")
    register_api_provider(_FakeProvider("two"), source_id="b")

    unregister_api_providers("a")

    assert get_api_provider("one") is None
    assert get_api_provider("two") is not None
    assert [provider.api for provider in get_api_providers()] == ["two"]


def test_registry_rejects_mismatched_model_api(restore_api_providers) -> None:
    clear_api_providers()
    register_api_provider(_FakeProvider("right-api"), source_id="test")
    provider = get_api_provider("right-api")
    assert provider is not None

    model = make_litellm_model(provider="openai", model_id="gpt-test", api="wrong-api")
    with pytest.raises(ValueError, match="Mismatched api"):
        provider.stream(model, _context())
