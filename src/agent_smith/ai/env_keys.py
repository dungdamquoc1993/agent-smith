"""Resolve API keys from environment."""

from __future__ import annotations

import os
from typing import Mapping

from agent_smith.ai.types import Provider

_PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
}


def get_env_api_key(provider: Provider, env: Mapping[str, str] | None = None) -> str | None:
    source = env if env is not None else os.environ
    for key in _PROVIDER_ENV_KEYS.get(provider, []):
        value = source.get(key)
        if value and value.strip():
            return value.strip()
    return None
