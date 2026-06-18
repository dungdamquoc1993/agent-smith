"""Tests for faux AI provider."""

from __future__ import annotations

import time

import pytest

from agent_smith.ai import (
    Context,
    UserMessage,
    complete,
    faux_response,
    faux_text,
    faux_thinking,
    faux_tool_call,
    get_model,
    set_faux_responses,
    stream,
)


@pytest.fixture(autouse=True)
def _bootstrap():
    from agent_smith.ai.providers.faux import register_faux_provider

    register_faux_provider()
    yield


@pytest.mark.asyncio
async def test_faux_text_stream_events():
    model = get_model("faux", "faux-1")
    assert model is not None

    set_faux_responses([faux_response([faux_text("Hi")])])

    context = Context(
        messages=[UserMessage(role="user", content="Hello", timestamp=int(time.time() * 1000))]
    )

    event_types: list[str] = []
    s = stream(model, context)
    async for event in s:
        event_types.append(event.type)

    assert "start" in event_types
    assert "text_start" in event_types
    assert "text_delta" in event_types
    assert "text_end" in event_types
    assert "done" in event_types

    final = await s.result()
    assert final.stop_reason == "stop"
    assert any(b.type == "text" and "Hi" in b.text for b in final.content)


@pytest.mark.asyncio
async def test_faux_thinking_and_tool_call():
    model = get_model("faux", "faux-1")
    assert model is not None

    set_faux_responses(
        [
            faux_response(
                [
                    faux_thinking("plan"),
                    faux_tool_call("echo", {"text": "ping"}),
                ],
                stop_reason="toolUse",
            )
        ]
    )

    context = Context(
        messages=[UserMessage(role="user", content="echo", timestamp=int(time.time() * 1000))]
    )

    final = await complete(model, context)
    assert final.stop_reason == "toolUse"
    assert any(b.type == "thinking" for b in final.content)
    tool_blocks = [b for b in final.content if b.type == "toolCall"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0].name == "echo"
    assert tool_blocks[0].arguments == {"text": "ping"}


@pytest.mark.asyncio
async def test_faux_empty_queue_errors():
    model = get_model("faux", "faux-1")
    assert model is not None

    set_faux_responses([])

    context = Context(
        messages=[UserMessage(role="user", content="?", timestamp=int(time.time() * 1000))]
    )

    final = await complete(model, context)
    assert final.stop_reason == "error"
    assert final.error_message == "No more faux responses queued"
