from __future__ import annotations

import time
from typing import Any

import pytest

from ai.events import create_assistant_message_event_stream
from ai.models import make_litellm_model
from ai.types import (
    AssistantMessage,
    AssistantMessageEventDone,
    AssistantMessageEventStart,
    AssistantMessageEventTextDelta,
    AssistantMessageEventTextEnd,
    AssistantMessageEventTextStart,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from agent import AgentHarness, MemorySessionRepo
from agent.harness.compaction import (
    COMPACTION_SUMMARY_PREFIX,
    MICROCOMPACT_MARKER,
    CompactionSettings,
    MicrocompactSettings,
    microcompact_messages,
    prepare_compaction,
)


def _now() -> int:
    return int(time.time() * 1000)


def _model(model_id: str = "gpt-test", context_window: int = 128_000) -> Model:
    return make_litellm_model(provider="openai", model_id=model_id).model_copy(
        update={"context_window": context_window}
    )


def _user(text: str = "hello") -> UserMessage:
    return UserMessage(content=text, timestamp=_now())


def _assistant(
    text: str,
    response_id: str | None = None,
    stop_reason: str = "stop",
) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="litellm",
        provider="openai",
        model="gpt-test",
        response_id=response_id,
        stop_reason=stop_reason,
        timestamp=_now(),
    )


def _tool_result(tool_call_id: str, text: str) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=tool_call_id,
        tool_name="read_file",
        content=[TextContent(text=text)],
        timestamp=_now(),
    )


def _stream_for(message: AssistantMessage):
    stream = create_assistant_message_event_stream()

    async def produce() -> None:
        partial = message.model_copy(update={"content": []}, deep=True)
        stream.push(AssistantMessageEventStart(partial=partial))
        for index, block in enumerate(message.content):
            partial = message.model_copy(update={"content": message.content[: index + 1]}, deep=True)
            stream.push(AssistantMessageEventTextStart(content_index=index, partial=partial))
            stream.push(
                AssistantMessageEventTextDelta(
                    content_index=index,
                    delta=block.text,
                    partial=partial,
                )
            )
            stream.push(
                AssistantMessageEventTextEnd(
                    content_index=index,
                    content=block.text,
                    partial=partial,
                )
            )
        stream.push(AssistantMessageEventDone(reason="stop", message=message))

    stream.set_producer(produce())
    return stream


@pytest.mark.asyncio
async def test_session_replay_uses_latest_compaction_projection() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")

    first_id = await session.append_message(_user("old"))
    second_id = await session.append_message(_assistant("keep me", response_id="r1"))
    await session.append_message(_user("also keep"))
    await session.append_compaction(
        summary="summary checkpoint",
        first_kept_entry_id=second_id,
        tokens_before=100,
    )
    await session.append_message(_assistant("after compact", response_id="r2"))

    context = await session.build_context()
    entries = await session.get_entries()

    assert first_id == entries[0].id
    assert [message.role for message in context.messages] == ["user", "assistant", "user", "assistant"]
    assert isinstance(context.messages[0], UserMessage)
    assert context.messages[0].content.startswith(COMPACTION_SUMMARY_PREFIX)
    assert context.messages[1].content[0].text == "keep me"
    assert context.messages[-1].content[0].text == "after compact"


def test_microcompact_truncates_old_tool_results_without_mutating() -> None:
    old = _tool_result("call-old", "x" * 80)
    recent = _tool_result("call-recent", "y" * 80)
    messages = [_user("hi"), old, recent]

    compacted = microcompact_messages(
        messages,
        MicrocompactSettings(tool_result_max_chars=10, keep_recent_tool_results=1),
    )

    assert compacted[1].content[0].text == MICROCOMPACT_MARKER
    assert compacted[1].details["microcompact"]["originalChars"] == 80
    assert compacted[2].content[0].text == "y" * 80
    assert old.content[0].text == "x" * 80


@pytest.mark.asyncio
async def test_prepare_compaction_chooses_recent_api_round_suffix() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")

    await session.append_message(_user("old " * 200))
    await session.append_message(_assistant("old answer " * 100, response_id="round-1"))
    await session.append_message(_user("middle prompt " * 80))
    kept_id = await session.append_message(_assistant("recent answer", response_id="round-2"))

    preparation = prepare_compaction(
        await session.get_branch(),
        CompactionSettings(reserve_tokens=10, keep_recent_tokens=2),
    )

    assert preparation is not None
    assert preparation.first_kept_entry_id == kept_id
    assert [message.role for message in preparation.messages_to_summarize] == [
        "user",
        "assistant",
        "user",
    ]


@pytest.mark.asyncio
async def test_harness_compact_with_hook_summary_appends_entry_and_emits_events() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    await session.append_message(_user("old " * 200))
    await session.append_message(_assistant("old answer " * 100, response_id="round-1"))
    await session.append_message(_user("recent prompt"))
    await session.append_message(_assistant("recent answer", response_id="round-2"))
    events: list[str] = []

    harness = AgentHarness(session=session, model=_model())
    harness.subscribe(lambda event: events.append(event.type))
    harness.on("session_before_compact", lambda event: {"summary": "hook summary"})

    result = await harness.compact(settings=CompactionSettings(reserve_tokens=10, keep_recent_tokens=2))
    context = await session.build_context()
    entries = await session.get_entries()

    assert result is not None
    assert result.summary == "hook summary"
    assert entries[-1].type == "compaction"
    assert context.messages[0].content.startswith(COMPACTION_SUMMARY_PREFIX)
    assert "session_before_compact" in events
    assert "session_compact" in events


@pytest.mark.asyncio
async def test_harness_auto_compact_before_provider_request() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    await session.append_message(_user("old " * 300))
    await session.append_message(_assistant("old answer " * 100, response_id="round-1"))
    await session.append_message(_user("recent prompt"))
    await session.append_message(_assistant("recent answer", response_id="round-2"))
    seen_contexts: list[Context] = []
    events: list[Any] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        seen_contexts.append(context)
        return _stream_for(_assistant("done", response_id="round-3"))

    harness = AgentHarness(
        session=session,
        model=_model(context_window=50),
        stream_fn=stream_fn,
        compaction_settings=CompactionSettings(reserve_tokens=20, keep_recent_tokens=2),
    )
    harness.subscribe(lambda event: events.append(event))
    harness.on("session_before_compact", lambda event: {"summary": "auto summary"})

    response = await harness.prompt("new prompt")

    assert response.content[0].text == "done"
    assert seen_contexts
    assert seen_contexts[0].messages[0].content.startswith(COMPACTION_SUMMARY_PREFIX)
    assert seen_contexts[0].messages[-1].content == "new prompt"
    compact_events = [event for event in events if event.type == "session_compact"]
    assert compact_events
    assert compact_events[0].trigger == "auto"
