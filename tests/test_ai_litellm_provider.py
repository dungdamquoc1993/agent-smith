"""Unit tests for the LiteLLM adapter without network calls."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from ai import Context, SimpleStreamOptions, StreamOptions, Tool, UserMessage, stream
from ai.models import make_litellm_model
from ai.providers import litellm_provider


class _FakeLiteLLMStream:
    def __init__(self, chunks: list[Any], response_id: str = "response-0") -> None:
        self.id = response_id
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def _context(tools: bool = False) -> Context:
    return Context(
        messages=[
            UserMessage(role="user", content="hello", timestamp=int(time.time() * 1000)),
        ],
        tools=[
            Tool(
                name="do_it",
                description="Do it",
                parameters={"type": "object", "properties": {"x": {"type": "number"}}},
            )
        ]
        if tools
        else None,
    )


def _chunk(delta: dict[str, Any] | None = None, finish_reason: str | None = None, **extra: Any):
    payload = {
        "id": extra.pop("id", None),
        "model": extra.pop("model", None),
        "choices": [{"delta": delta or {}, "finish_reason": finish_reason}],
        **extra,
    }
    return payload


@pytest.mark.asyncio
async def test_litellm_forwards_options_and_supports_ad_hoc_model(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeLiteLLMStream(
            [
                _chunk({"content": "hello"}, id="chunk-1", model="resolved-model"),
                _chunk(
                    {},
                    finish_reason="stop",
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "total_tokens": 13,
                        "prompt_tokens_details": {"cached_tokens": 4},
                    },
                ),
            ],
            response_id="response-1",
        )

    monkeypatch.setattr(litellm_provider.litellm, "acompletion", fake_acompletion)

    model = make_litellm_model(
        provider="new-provider",
        model_id="new-model",
        litellm_model="new-provider/new-model",
        headers={"x-model": "model", "x-shared": "model"},
        provider_options={"custom_model_option": "model-default", "temperature": 0.1},
    )
    stream = litellm_provider.LitellmApiProvider().stream(
        model,
        _context(),
        StreamOptions(
            temperature=0.2,
            max_tokens=32,
            api_key="explicit-key",
            timeout_ms=1500,
            max_retries=3,
            max_retry_delay_ms=2500,
            headers={"x-option": "option", "x-shared": "option"},
            metadata={"trace": "abc"},
            cache_retention="long",
            session_id="session-1",
            provider_options={"custom_option": "request"},
            extra_flag=True,
        ),
    )

    final = await stream.result()

    assert final.content[0].text == "hello"
    assert final.response_model == "resolved-model"
    assert final.response_id == "chunk-1"
    assert final.usage.cache_read == 4
    assert captured["model"] == "new-provider/new-model"
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 32
    assert captured["api_key"] == "explicit-key"
    assert captured["timeout"] == 1.5
    assert captured["num_retries"] == 3
    assert captured["max_retry_delay"] == 2.5
    assert captured["metadata"] == {"trace": "abc"}
    assert captured["cache_retention"] == "long"
    assert captured["session_id"] == "session-1"
    assert captured["custom_model_option"] == "model-default"
    assert captured["custom_option"] == "request"
    assert captured["extra_flag"] is True
    assert captured["headers"] == {
        "x-model": "model",
        "x-option": "option",
        "x-shared": "option",
    }


@pytest.mark.asyncio
async def test_google_vertex_credentials_take_precedence_over_env_api_key(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    credentials_path = tmp_path / "service-account.json"
    credentials_path.write_text('{"project_id": "vertex-project"}', encoding="utf-8")

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeLiteLLMStream([_chunk({"content": "ok"}, finish_reason="stop")])

    monkeypatch.setattr(litellm_provider.litellm, "acompletion", fake_acompletion)

    model = make_litellm_model(provider="google", model_id="gemini-2.5-flash")
    result = await stream(
        model,
        _context(),
        StreamOptions(
            env={
                "GOOGLE_APPLICATION_CREDENTIALS": str(credentials_path),
                "GEMINI_API_KEY": "env-api-key",
                "GOOGLE_CLOUD_LOCATION": "asia-southeast1",
            }
        ),
    ).result()

    assert result.stop_reason == "stop"
    assert captured["model"] == "vertex_ai/gemini-2.5-flash"
    assert "api_key" not in captured
    assert captured["vertex_project"] == "vertex-project"
    assert captured["vertex_location"] == "asia-southeast1"


@pytest.mark.asyncio
async def test_stream_simple_uses_model_thinking_level_map(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeLiteLLMStream([_chunk({"content": "ok"}, finish_reason="stop")])

    monkeypatch.setattr(litellm_provider.litellm, "acompletion", fake_acompletion)

    model = make_litellm_model(
        provider="openai",
        model_id="reasoning-model",
        thinking_level_map={"xhigh": "max"},
    )
    stream = litellm_provider.LitellmApiProvider().stream_simple(
        model,
        _context(),
        SimpleStreamOptions(reasoning="xhigh"),
    )

    await stream.result()

    assert captured["reasoning_effort"] == "max"


@pytest.mark.asyncio
async def test_stream_tool_index_and_single_thinking_end(monkeypatch) -> None:
    async def fake_acompletion(**kwargs):
        _ = kwargs
        return _FakeLiteLLMStream(
            [
                _chunk({"reasoning_content": "think"}),
                _chunk({"content": "answer"}),
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "function": {"name": "do_it", "arguments": "{\"x\":"},
                            }
                        ]
                    }
                ),
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": "1}"},
                            }
                        ]
                    },
                    finish_reason="tool_calls",
                ),
            ]
        )

    monkeypatch.setattr(litellm_provider.litellm, "acompletion", fake_acompletion)

    model = make_litellm_model(provider="openai", model_id="gpt-test")
    stream = litellm_provider.LitellmApiProvider().stream(model, _context(tools=True))
    events = [event async for event in stream]
    final = await stream.result()

    assert [block.type for block in final.content] == ["thinking", "text", "toolCall"]
    assert final.content[2].id == "call-1"
    assert final.content[2].name == "do_it"
    assert final.content[2].arguments == {"x": 1}
    assert final.stop_reason == "toolUse"
    assert sum(1 for event in events if event.type == "thinking_end") == 1
    assert [event.type for event in events].count("toolcall_start") == 1
