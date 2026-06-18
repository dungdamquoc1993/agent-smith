"""Resolve API keys and provider credentials from environment."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping, TypedDict

from agent_smith.ai.types import Provider

_PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
}


class GoogleVertexConfig(TypedDict):
    vertex_project: str
    vertex_location: str


def _env_source(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def get_env_api_key(provider: Provider, env: Mapping[str, str] | None = None) -> str | None:
    source = _env_source(env)
    for key in _PROVIDER_ENV_KEYS.get(provider, []):
        value = source.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _read_vertex_project(credentials_path: str) -> str | None:
    try:
        data = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    project_id = data.get("project_id")
    if isinstance(project_id, str) and project_id.strip():
        return project_id.strip()
    return None


def get_google_vertex_config(env: Mapping[str, str] | None = None) -> GoogleVertexConfig | None:
    """Vertex AI auth via service-account JSON (GOOGLE_APPLICATION_CREDENTIALS)."""
    source = _env_source(env)
    credentials_path = source.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path or not Path(credentials_path).is_file():
        return None

    project = source.get("GOOGLE_CLOUD_PROJECT") or source.get("VERTEXAI_PROJECT")
    if project:
        project = project.strip()
    else:
        project = _read_vertex_project(credentials_path)

    if not project:
        return None

    location = (
        source.get("GOOGLE_CLOUD_LOCATION")
        or source.get("VERTEXAI_LOCATION")
        or "us-central1"
    ).strip()

    return GoogleVertexConfig(vertex_project=project, vertex_location=location)


def is_provider_configured(provider: Provider, env: Mapping[str, str] | None = None) -> bool:
    if get_env_api_key(provider, env):
        return True
    if provider == "google" and get_google_vertex_config(env):
        return True
    return False
