"""Live integration tests for the OpenRouter-backed model catalog."""

from __future__ import annotations

import time

import pytest

from agent_smith.core.llm import Context, StreamOptions, UserMessage, get_model, stream
from agent_smith.core.llm.env_keys import is_provider_configured
from agent_smith.core.llm.types import Provider


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
async def test_openrouter_stream_and_complete():
    if not is_provider_configured("openrouter"):
        pytest.skip("OPENROUTER_API_KEY not configured")

    await _assert_text_completion(
        "openrouter",
        "openai/gpt-5.4-nano",
        "Say hello in three words.",
    )


def test_multiple_model_families_route_through_openrouter() -> None:
    gpt_model = get_model("openrouter", "openai/gpt-5.5")
    claude_model = get_model("openrouter", "anthropic/claude-sonnet-5")
    gemini_model = get_model("openrouter", "google/gemini-3.5-flash")

    assert gpt_model is not None
    assert claude_model is not None
    assert gemini_model is not None
    assert {gpt_model.provider, claude_model.provider, gemini_model.provider} == {"openrouter"}
