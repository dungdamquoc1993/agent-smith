from __future__ import annotations

import time
import uuid
from os import getenv
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.agent import (
    AgentHarness,
    AgentHarnessResources,
    AgentTool,
    AgentToolResult,
    MemorySessionRepo,
    PostgresSessionRepo,
    PromptTemplate,
    Skill,
    format_prompt_template_invocation,
    format_skill_invocation,
    format_skills_for_system_prompt,
)
from agent_smith.db.base import Base
from agent_smith.db.models.principal import Principal, PrincipalType
from agent_smith.ai.events import create_assistant_message_event_stream
from agent_smith.ai.models import make_litellm_model
from agent_smith.ai.types import (
    AssistantMessage,
    AssistantMessageEventDone,
    AssistantMessageEventStart,
    AssistantMessageEventTextDelta,
    AssistantMessageEventTextEnd,
    AssistantMessageEventTextStart,
    AssistantMessageEventToolcallEnd,
    AssistantMessageEventToolcallStart,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    UserMessage,
)


def _now() -> int:
    return int(time.time() * 1000)


def _model(model_id: str = "gpt-test") -> Model:
    return make_litellm_model(provider="openai", model_id=model_id)


def _user(text: str = "hello") -> UserMessage:
    return UserMessage(content=text, timestamp=_now())


def _assistant(content: list[Any], stop_reason: str = "stop") -> AssistantMessage:
    return AssistantMessage(
        content=content,
        api="litellm",
        provider="openai",
        model="gpt-test",
        stop_reason=stop_reason,
        timestamp=_now(),
    )


def _stream_for(message: AssistantMessage):
    stream = create_assistant_message_event_stream()

    async def produce() -> None:
        partial = message.model_copy(update={"content": []}, deep=True)
        stream.push(AssistantMessageEventStart(partial=partial))
        for index, block in enumerate(message.content):
            partial = message.model_copy(update={"content": message.content[: index + 1]}, deep=True)
            if isinstance(block, TextContent):
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
            elif isinstance(block, ToolCall):
                stream.push(AssistantMessageEventToolcallStart(content_index=index, partial=partial))
                stream.push(
                    AssistantMessageEventToolcallEnd(
                        content_index=index,
                        tool_call=block,
                        partial=partial,
                    )
                )
        stream.push(
            AssistantMessageEventDone(
                reason="toolUse" if message.stop_reason == "toolUse" else "stop",
                message=message,
            )
        )

    stream.set_producer(produce())
    return stream


@pytest.mark.asyncio
async def test_memory_session_replays_branch_context() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1", title="demo")

    await session.append_model_change("openai", "gpt-test")
    await session.append_thinking_level_change("high")
    await session.append_active_tools_change(["read_file"])
    await session.append_message(_user("hi"))
    await session.append_message(_assistant([TextContent(text="hello")]))

    context = await session.build_context()

    assert [message.role for message in context.messages] == ["user", "assistant"]
    assert context.thinking_level == "high"
    assert context.model is not None
    assert context.model.provider == "openai"
    assert context.model.model_id == "gpt-test"
    assert context.active_tool_names == ["read_file"]


@pytest.mark.asyncio
async def test_postgres_session_repo_roundtrip_when_database_is_configured() -> None:
    database_url = getenv("AGENT_SMITH_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENT_SMITH_TEST_DATABASE_URL is not configured")

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        principal_id = uuid.uuid4()
        async with factory() as db, db.begin():
            db.add(
                Principal(
                    id=principal_id,
                    type=PrincipalType.human,
                    display_name="Harness Test",
                )
            )

        repo = PostgresSessionRepo(factory)
        session = await repo.create(principal_id=str(principal_id), title="harness")
        await session.append_message(_user("hi"))

        reopened = await repo.open(await session.get_metadata())
        context = await reopened.build_context()

        assert context.messages[0].role == "user"
        assert context.messages[0].content == "hi"
    finally:
        await engine.dispose()


def test_resources_format_skill_and_template() -> None:
    skill = Skill(
        name="debug",
        description="Debug problems",
        content="Read logs carefully.",
        file_path="/tmp/skills/debug/SKILL.md",
    )
    template = PromptTemplate(name="fix", content="Fix $1 using $@ and ${@:2}")

    assert "Read logs carefully." in format_skill_invocation(skill, "Be concise.")
    assert "<available_skills>" in format_skills_for_system_prompt([skill])
    assert format_prompt_template_invocation(template, ["bug", "tests"]) == (
        "Fix bug using bug tests and tests"
    )


@pytest.mark.asyncio
async def test_harness_prompt_persists_messages_and_provider_hook_options() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    captured_options: list[SimpleStreamOptions] = []
    events: list[str] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        assert model.id == "gpt-test"
        assert context.messages[0].role == "user"
        assert options is not None
        captured_options.append(options)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=stream_fn,
        stream_options={"headers": {"x-base": "1"}},
        get_api_key_and_headers=lambda model: {
            "apiKey": "secret",
            "headers": {"x-auth": model.provider},
        },
    )
    harness.subscribe(lambda event: events.append(event.type))

    async def before_request(event):
        assert event.stream_options.headers == {"x-base": "1", "x-auth": "openai"}
        return {"streamOptions": {"headers": {"x-base": "2", "x-extra": "3"}}}

    harness.on("before_provider_request", before_request)

    response = await harness.prompt("hello")
    entries = await session.get_entries()

    assert response.content[0].text == "done"
    assert [entry.type for entry in entries] == ["message", "message"]
    assert captured_options[0].api_key == "secret"
    assert captured_options[0].headers == {"x-base": "2", "x-auth": "openai", "x-extra": "3"}
    assert "save_point" in events
    assert events[-1] == "settled"


@pytest.mark.asyncio
async def test_harness_skill_and_prompt_template_invocations() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    prompts: list[str] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        last = context.messages[-1]
        assert isinstance(last, UserMessage)
        prompts.append(last.content if isinstance(last.content, str) else last.content[0].text)
        return _stream_for(_assistant([TextContent(text="ok")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=stream_fn,
        resources=AgentHarnessResources(
            skills=[
                Skill(
                    name="debug",
                    description="Debug",
                    content="Use the debugger.",
                    file_path="/skills/debug/SKILL.md",
                )
            ],
            prompt_templates=[PromptTemplate(name="fix", content="Fix $1")],
        ),
    )

    await harness.skill("debug")
    await harness.prompt_from_template("fix", ["tests"])

    assert "Use the debugger." in prompts[0]
    assert prompts[1] == "Fix tests"


@pytest.mark.asyncio
async def test_harness_executes_tool_and_applies_tool_hooks() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    calls = 0

    async def execute(tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text=f"{tool_call_id}:{params['x']}")])

    tool = AgentTool(
        name="do_it",
        label="Do it",
        description="Does it",
        parameters={
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
        },
        execute=execute,
    )
    first = _assistant(
        [ToolCall(id="call-1", name="do_it", arguments={"x": 1})],
        stop_reason="toolUse",
    )
    second = _assistant([TextContent(text="finished")])

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        nonlocal calls
        calls += 1
        return _stream_for(first if calls == 1 else second)

    harness = AgentHarness(session=session, model=_model(), tools=[tool], stream_fn=stream_fn)
    hook_events: list[str] = []
    harness.on("tool_call", lambda event: hook_events.append(event.type))
    harness.on(
        "tool_result",
        lambda event: {
            "content": [TextContent(text="patched")],
            "details": {"patched": True},
        },
    )

    await harness.prompt("hello")
    context = await session.build_context()

    assert calls == 2
    assert hook_events == ["tool_call"]
    assert [message.role for message in context.messages] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
    ]
    assert context.messages[2].content[0].text == "patched"
