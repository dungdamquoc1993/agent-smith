"""Integration tests for multi-provider AI layer (OpenAI + Google via LiteLLM)."""

from __future__ import annotations

import time

import pytest

from agent_smith.ai import Context, StreamOptions, UserMessage, get_model, stream
from agent_smith.ai.env_keys import is_provider_configured
from agent_smith.ai.types import Provider


def _simple_context(prompt: str) -> Context:
    return Context(
        system_prompt="Reply briefly in one short sentence.",
        messages=[UserMessage(role="user", content=prompt, timestamp=int(time.time() * 1000))],
    )


async def _assert_text_completion(provider: Provider, model_id: str, prompt: str) -> None:
    model = get_model(provider, model_id)
    assert model is not None
    assert model.api == "litellm"
    assert model.provider == provider

    event_types: list[str] = []
    s = stream(model, _simple_context(prompt), options=StreamOptions(max_tokens=64))
    async for event in s:
        event_types.append(event.type)

    assert "start" in event_types
    assert "done" in event_types

    final = await s.result()
    assert final.stop_reason == "stop", final.error_message
    assert final.provider == provider
    text_blocks = [b for b in final.content if b.type == "text"]
    assert text_blocks, "expected at least one text block"
    assert text_blocks[0].text.strip()


@pytest.mark.asyncio
async def test_openai_stream_and_complete():
    if not is_provider_configured("openai"):
        pytest.skip("OPENAI_API_KEY not configured")

    await _assert_text_completion("openai", "gpt-4o-mini", "Say hello in three words.")


@pytest.mark.asyncio
async def test_google_stream_and_complete():
    if not is_provider_configured("google"):
        pytest.skip("Google credentials not configured (GEMINI_API_KEY or GOOGLE_APPLICATION_CREDENTIALS)")

    await _assert_text_completion("google", "gemini-2.5-flash", "Say hello in three words.")


@pytest.mark.asyncio
async def test_multi_provider_catalog():
    """Both providers share the same litellm API transport (pi-style multi-provider)."""
    openai_model = get_model("openai", "gpt-4o-mini")
    google_model = get_model("google", "gemini-2.5-flash")

    assert openai_model is not None
    assert google_model is not None
    assert openai_model.api == google_model.api == "litellm"
    assert openai_model.provider != google_model.provider
