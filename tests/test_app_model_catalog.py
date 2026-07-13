"""Model switcher catalog tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_smith.app.services.agent_runs import AgentRunService
from agent_smith.core.llm import (
    clear_models,
    get_model_by_key,
    get_models,
    get_providers,
    make_litellm_model,
    register_model,
    register_models,
)
from agent_smith.infra.config import Settings

PROVIDER_ENV_KEYS = ("OPENROUTER_API_KEY",)


@pytest.fixture()
def restore_models():
    snapshot = [
        model.model_copy(deep=True)
        for provider in get_providers()
        for model in get_models(provider)
    ]
    yield
    clear_models()
    register_models(snapshot)


def _service(default_model_key: str = "gpt-5.5") -> AgentRunService:
    return AgentRunService(
        session_service=MagicMock(),
        resource_service=MagicMock(),
        default_permission_mode="default",
        default_model_key=default_model_key,
    )


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_model_and_postgres_defaults_are_deployment_config(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SMITH_DEFAULT_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("AGENT_SMITH_POSTGRES_URL", "postgresql+asyncpg://example.test/smith")

    settings = Settings(_env_file=None)

    assert settings.default_model == "gpt-5.4-mini"
    assert settings.postgres_url == "postgresql+asyncpg://example.test/smith"


def test_model_choices_come_from_catalog_and_hide_unconfigured_providers(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    service = _service()

    assert service.model_choices() == []
    assert service.default_model_selection() == ""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    choices = service.model_choices()

    assert len(choices) == 19
    assert any(choice["key"] == "gpt-5.5" for choice in choices)
    assert all("provider" not in choice and "modelId" not in choice for choice in choices)
    assert service.default_model_selection() == "gpt-5.5"
    assert service._selected_model("gpt-5.5") == get_model_by_key("gpt-5.5")
    assert service._selected_model("gpt-5.5").id == "openai/gpt-5.5"


def test_openrouter_catalog_models_are_enabled_by_openrouter_key(
    monkeypatch,
    restore_models,
) -> None:
    _clear_provider_env(monkeypatch)
    register_model(
        make_litellm_model(
            provider="openrouter",
            model_id="vendor/example-model",
            key="example-model",
            name="Example via OpenRouter",
        )
    )
    service = _service()

    assert service.model_choices() == []

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    assert {
        "key": "example-model",
        "label": "Example via OpenRouter",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 128_000,
        "maxTokens": 16_384,
    } in service.model_choices()
    assert service._selected_model("example-model").provider == "openrouter"
