from __future__ import annotations

import time
import uuid
from os import getenv
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.core.agent import (
    AgentCatalogEntry,
    AgentHarness,
    AgentHarnessPromptOptions,
    AgentHarnessResources,
    AgentTool,
    AgentToolResult,
    PromptTemplate,
    RecentConversationSnapshot,
    Skill,
    UserMemorySnapshot,
    format_recent_conversations_for_context,
    format_prompt_template_invocation,
    format_skill_invocation,
    format_skills_for_system_prompt,
    format_skills_for_system_reminder,
    format_user_memory_for_system_reminder,
)
from agent_smith.infra.storage.postgres.adapters.sessions import PostgresSessionCatalog
from agent_smith.infra.storage.postgres.database import Base
from agent_smith.infra.storage.postgres.models.principals import Principal
from agent_smith.core.llm.events import create_assistant_message_event_stream
from agent_smith.core.llm.models import make_litellm_model
from agent_smith.core.llm.types import (
    AssistantMessage,
    AssistantMessageEventDone,
    AssistantMessageEventStart,
    AssistantMessageEventTextDelta,
    AssistantMessageEventTextEnd,
    AssistantMessageEventTextStart,
    AssistantMessageEventToolcallEnd,
    AssistantMessageEventToolcallStart,
    Context,
    ImageContent,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    UserMessage,
)
from helpers.sessions import MemoryRecentConversationProvider, MemorySessionRepo


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
async def test_memory_session_metadata_tracks_provenance_and_fork_overrides() -> None:
    repo = MemorySessionRepo()
    main = await repo.create(principal_id="principal-1", title="main")
    main_metadata = await main.get_metadata()

    assert main_metadata.kind == "chat"
    assert main_metadata.provenance == {}

    child = await repo.create(
        principal_id="principal-1",
        title="child",
        kind="agent_run",
        parent_session_id=main_metadata.id,
        agent_name="reviewer",
        origin_task_id="task-1",
        provenance={"mode": "sync"},
    )
    reopened = await repo.open(await child.get_metadata())
    child_metadata = await reopened.get_metadata()

    assert child_metadata.kind == "agent_run"
    assert child_metadata.parent_session_id == main_metadata.id
    assert child_metadata.agent_name == "reviewer"
    assert child_metadata.origin_task_id == "task-1"
    assert child_metadata.provenance == {"mode": "sync"}

    fork = await repo.fork(await child.get_metadata(), provenance={"mode": "async"})
    fork_metadata = await fork.get_metadata()

    assert fork_metadata.kind == "agent_run"
    assert fork_metadata.parent_session_id == main_metadata.id
    assert fork_metadata.agent_name == "reviewer"
    assert fork_metadata.origin_task_id == "task-1"
    assert fork_metadata.provenance == {"mode": "async"}


@pytest.mark.asyncio
async def test_postgres_session_repo_roundtrip_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")

    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        principal_id = uuid.uuid4()
        async with factory() as db, db.begin():
            db.add(
                Principal(
                    id=principal_id,
                    display_name="Harness Test",
                )
            )

        repo = PostgresSessionCatalog(factory)
        session = await repo.create(principal_id=str(principal_id), title="harness")
        await session.append_message(_user("hi"))

        reopened = await repo.open(await session.get_metadata())
        reopened_metadata = await reopened.get_metadata()
        context = await reopened.build_context()

        assert reopened_metadata.kind == "chat"
        assert reopened_metadata.provenance == {}
        assert context.messages[0].role == "user"
        assert context.messages[0].content == "hi"

        child = await repo.create(
            principal_id=str(principal_id),
            title="child",
            kind="agent_run",
            parent_session_id=reopened_metadata.id,
            agent_name="reviewer",
            origin_task_id="task-1",
            provenance={"mode": "sync"},
        )
        child_metadata = await (await repo.open(await child.get_metadata())).get_metadata()

        assert child_metadata.kind == "agent_run"
        assert child_metadata.parent_session_id == reopened_metadata.id
        assert child_metadata.agent_name == "reviewer"
        assert child_metadata.origin_task_id == "task-1"
        assert child_metadata.provenance == {"mode": "sync"}
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
    assert format_skills_for_system_reminder([skill]).startswith("<system-reminder>")
    assert "<user-knowledge-memory>" in format_user_memory_for_system_reminder(
        UserMemorySnapshot(content="User prefers concise replies.")
    )
    assert format_prompt_template_invocation(template, ["bug", "tests"]) == (
        "Fix bug using bug tests and tests"
    )


@pytest.mark.asyncio
async def test_harness_prompt_accepts_prompt_options_model_with_images() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    seen_content: list[Any] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        last = context.messages[-1]
        assert isinstance(last, UserMessage)
        seen_content.append(last.content)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(session=session, model=_model(), stream_fn=stream_fn)
    await harness.prompt(
        "describe",
        AgentHarnessPromptOptions(
            images=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
        ),
    )

    assert isinstance(seen_content[0], list)
    assert seen_content[0][0].text == "describe"
    assert seen_content[0][1].mime_type == "image/png"


@pytest.mark.asyncio
async def test_runtime_image_overlay_survives_tool_turn_but_persistence_uses_marker() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")

    async def execute(tool_call_id, params, signal=None, on_update=None):
        _ = tool_call_id, params, signal, on_update
        return AgentToolResult(content=[TextContent(text="tool output")])

    tool = AgentTool(
        name="inspect",
        label="Inspect",
        description="Inspect",
        parameters={"type": "object", "properties": {}},
        execute=execute,
    )
    responses = [
        _assistant(
            [ToolCall(id="call-1", name="inspect", arguments={})],
            stop_reason="toolUse",
        ),
        _assistant([TextContent(text="done")]),
    ]
    provider_user_content: list[Any] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        user = next(message for message in context.messages if message.role == "user")
        provider_user_content.append(user.content)
        return _stream_for(responses[len(provider_user_content) - 1])

    harness = AgentHarness(session=session, model=_model(), tools=[tool], stream_fn=stream_fn)
    await harness.prompt(
        "describe",
        AgentHarnessPromptOptions(
            images=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
        ),
    )

    assert len(provider_user_content) == 2
    assert all(isinstance(content[1], ImageContent) for content in provider_user_content)
    persisted_user = (await session.get_entries())[0].message
    assert persisted_user is not None and isinstance(persisted_user.content, list)
    assert persisted_user.content[1].type == "text"
    assert "Runtime image omitted" in persisted_user.content[1].text


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


@pytest.mark.asyncio
async def test_harness_pending_mutations_flush_after_turn() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    harness: AgentHarness | None = None

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, context, options
        stream = create_assistant_message_event_stream()

        async def produce() -> None:
            assert harness is not None
            await harness.set_thinking_level("high")
            await harness.set_active_tools([])
            message = _assistant([TextContent(text="done")])
            stream.push(AssistantMessageEventStart(partial=message.model_copy(update={"content": []})))
            stream.push(AssistantMessageEventDone(reason="stop", message=message))

        stream.set_producer(produce())
        return stream

    tool = AgentTool(
        name="noop",
        label="Noop",
        description="Noop",
        parameters={"type": "object", "properties": {}},
        execute=lambda tool_call_id, params, signal=None, on_update=None: AgentToolResult(
            content=[TextContent(text="noop")]
        ),
    )
    harness = AgentHarness(session=session, model=_model(), tools=[tool], stream_fn=stream_fn)

    await harness.prompt("hello")
    entries = await session.get_entries()
    context = await session.build_context()

    assert [entry.type for entry in entries] == [
        "message",
        "message",
        "thinking_level_change",
        "active_tools_change",
    ]
    assert context.thinking_level == "high"
    assert context.active_tool_names == []


@pytest.mark.asyncio
async def test_harness_surfaces_skill_catalog_as_system_reminder_user_message() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    captured_contexts: list[Context] = []

    def execute(tool_call_id, params, signal=None, on_update=None):
        _ = tool_call_id, params, signal, on_update
        return AgentToolResult(content=[TextContent(text="ok")])

    skills_tool = AgentTool(
        name="skill",
        label="Skill",
        description="Execute a skill by name with optional arguments.",
        parameters={"type": "object", "properties": {}},
        execute=execute,
    )

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        captured_contexts.append(context)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        system_prompt="Review carefully.",
        stream_fn=stream_fn,
        tools=[skills_tool],
        resources=AgentHarnessResources(
            skills=[
                Skill(
                    name="debug",
                    description="Debug problems",
                    content="Use the debugger.",
                    file_path="/skills/debug/SKILL.md",
                ),
                Skill(
                    name="hidden",
                    description="Hidden",
                    content="Do not surface.",
                    file_path="/skills/hidden/SKILL.md",
                    disable_model_invocation=True,
                ),
            ],
        ),
    )

    await harness.prompt("hello")

    provider_context = captured_contexts[0]
    assert provider_context.system_prompt == "Review carefully."
    assert provider_context.tools is not None
    assert provider_context.tools[0].description == skills_tool.description
    assert isinstance(provider_context.messages[0], UserMessage)
    assert isinstance(provider_context.messages[-1], UserMessage)
    assert provider_context.messages[0].content.startswith("<system-reminder>")
    assert "debug" in provider_context.messages[0].content
    assert "hidden" not in provider_context.messages[0].content
    assert provider_context.messages[-1].content == "hello"

    persisted = await session.build_context()
    assert [message.role for message in persisted.messages] == ["user", "assistant"]
    assert persisted.messages[0].content == "hello"


@pytest.mark.asyncio
async def test_harness_surfaces_agent_catalog_delta_as_system_reminder() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    captured_contexts: list[Context] = []

    def execute(tool_call_id, params, signal=None, on_update=None):
        _ = tool_call_id, params, signal, on_update
        return AgentToolResult(content=[TextContent(text="ok")])

    task_tool = AgentTool(
        name="task",
        label="Task",
        description="Run a named agent task.",
        parameters={"type": "object", "properties": {}},
        execute=execute,
    )

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        captured_contexts.append(context)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        system_prompt="Coordinate work.",
        stream_fn=stream_fn,
        tools=[task_tool],
        resources=AgentHarnessResources(
            agent_catalog=[
                AgentCatalogEntry(
                    name="reviewer",
                    description="Review changes",
                    when_to_use="Use for code review",
                    tools_allow=["read_file"],
                )
            ],
        ),
    )

    await harness.prompt("hello")

    provider_context = captured_contexts[0]
    assert isinstance(provider_context.messages[0], UserMessage)
    assert provider_context.messages[0].content.startswith("<system-reminder>")
    assert "agent-catalog-delta" in provider_context.messages[0].content
    assert "reviewer" in provider_context.messages[0].content
    assert provider_context.messages[-1].content == "hello"


@pytest.mark.asyncio
async def test_harness_injects_user_memory_snapshot_as_runtime_reminder() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    captured_contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        captured_contexts.append(context)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=stream_fn,
        resources=AgentHarnessResources(
            user_memory=UserMemorySnapshot(
                content="User prefers concise replies.",
                source="resource:user_memory/default",
                resource_id="memory-1",
                resource_version_id="version-1",
                version=1,
                content_hash="abc",
            ),
        ),
    )

    await harness.prompt("hello")

    provider_context = captured_contexts[0]
    entries = await session.get_entries()
    assert isinstance(provider_context.messages[0], UserMessage)
    assert "<user-knowledge-memory>" in provider_context.messages[0].content
    assert "User prefers concise replies." in provider_context.messages[0].content
    assert provider_context.messages[-1].content == "hello"
    assert [entry.type for entry in entries] == ["custom", "message", "message"]
    assert entries[0].custom_type == "user_memory_snapshot"
    assert entries[0].data["content"] == "User prefers concise replies."


@pytest.mark.asyncio
async def test_harness_context_frame_orders_metadata_recent_and_user_knowledge() -> None:
    repo = MemorySessionRepo()
    previous = await repo.create(principal_id="principal-1", title="Previous chat")
    await previous.append_message(_user("Earlier request"))
    await previous.append_message(_assistant([TextContent(text="Earlier answer")]))
    session = await repo.create(principal_id="principal-1", title="Current chat")
    captured_contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        captured_contexts.append(context)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=stream_fn,
        context_metadata={"surface": {"kind": "web", "page": "Agent Lab"}},
        recent_conversation_provider=MemoryRecentConversationProvider(repo),
        resources=AgentHarnessResources(
            user_memory=UserMemorySnapshot(
                content="Project Goal\n------------\n- Build Agent Smith."
            ),
        ),
    )

    await harness.prompt("hello")

    provider_context = captured_contexts[0]
    assert "runtime-metadata-snapshot" in provider_context.messages[0].content
    assert "recent-conversations" in provider_context.messages[1].content
    assert "Previous chat" in provider_context.messages[1].content
    assert "<user-knowledge-memory>" in provider_context.messages[2].content
    assert provider_context.messages[-1].content == "hello"

    persisted = await session.build_context()
    assert [message.role for message in persisted.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_harness_snapshots_runtime_metadata_once_per_session() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    captured_contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        captured_contexts.append(context)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=stream_fn,
        context_metadata={"surface": "first"},
    )

    await harness.prompt("first")
    harness.context_metadata = {"surface": "second"}
    await harness.prompt("second")

    entries = await session.get_entries()
    snapshots = [
        entry
        for entry in entries
        if entry.type == "custom" and entry.custom_type == "runtime_metadata_snapshot"
    ]
    assert len(snapshots) == 1
    assert snapshots[0].data == {"surface": "first"}
    assert "surface: first" in captured_contexts[0].messages[0].content
    assert "surface: first" in captured_contexts[1].messages[0].content
    assert "surface: second" not in captured_contexts[1].messages[0].content


@pytest.mark.asyncio
async def test_harness_injects_turn_context_metadata_without_snapshotting_it() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    captured_contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        captured_contexts.append(context)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=stream_fn,
        context_metadata={"actor": {"principalId": "principal-1"}},
    )

    await harness.prompt("first", {"turnContextMetadata": {"surface": {"userAgent": "first"}}})
    await harness.prompt("second", {"turnContextMetadata": {"surface": {"userAgent": "second"}}})

    assert "runtime-metadata-snapshot" in captured_contexts[0].messages[0].content
    assert "runtime-invocation-metadata" in captured_contexts[0].messages[1].content
    assert "userAgent: first" in captured_contexts[0].messages[1].content
    assert "userAgent: second" in captured_contexts[1].messages[1].content
    assert "userAgent: first" not in captured_contexts[1].messages[1].content

    entries = await session.get_entries()
    turn_snapshots = [
        entry
        for entry in entries
        if entry.type == "custom" and entry.custom_type == "runtime_invocation_metadata"
    ]
    assert turn_snapshots == []


@pytest.mark.asyncio
async def test_memory_recent_conversation_provider_scopes_to_same_principal_chats() -> None:
    repo = MemorySessionRepo()
    first = await repo.create(principal_id="principal-1", title="Include me")
    await first.append_message(_user("Useful context"))
    await repo.create(principal_id="principal-2", title="Other principal")
    await repo.create(principal_id="principal-1", kind="agent_run", title="Agent run")
    current = await repo.create(principal_id="principal-1", title="Current")
    current_metadata = await current.get_metadata()

    provider = MemoryRecentConversationProvider(repo)
    results = await provider.get_recent_conversations(
        principal_id="principal-1",
        current_session_id=current_metadata.id,
        limit=40,
    )

    assert [result.title for result in results] == ["Include me"]
    assert results[0].messages[0].content == "Useful context"


def test_recent_conversation_renderer_truncates_long_conversations_without_summary() -> None:
    messages = []
    for index in range(8):
        messages.append(_user(f"user {index}"))
        messages.append(_assistant([TextContent(text=f"assistant {index}")]))

    rendered = format_recent_conversations_for_context(
        [
            RecentConversationSnapshot(
                id="session-1",
                title="Long chat",
                messages=messages,
            )
        ]
    )

    assert "<<Convo too long truncate>>" in rendered
    assert "User: user 0" in rendered
    assert "AI: assistant 7" in rendered
    assert "AI Summary" not in rendered


@pytest.mark.asyncio
async def test_harness_reuses_frozen_user_memory_snapshot_for_session() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    captured_contexts: list[Context] = []

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        _ = model, options
        captured_contexts.append(context)
        return _stream_for(_assistant([TextContent(text="done")]))

    harness = AgentHarness(
        session=session,
        model=_model(),
        stream_fn=stream_fn,
        resources=AgentHarnessResources(
            user_memory=UserMemorySnapshot(
                content="Original memory.",
                resource_version_id="version-1",
                version=1,
            ),
        ),
    )

    await harness.prompt("first")
    await harness.set_resources(
        AgentHarnessResources(
            user_memory=UserMemorySnapshot(
                content="Updated memory.",
                resource_version_id="version-2",
                version=2,
            )
        )
    )
    await harness.prompt("second")

    entries = await session.get_entries()
    snapshots = [
        entry
        for entry in entries
        if entry.type == "custom" and entry.custom_type == "user_memory_snapshot"
    ]
    assert len(snapshots) == 1
    assert "Original memory." in captured_contexts[0].messages[0].content
    assert "Original memory." in captured_contexts[1].messages[0].content
    assert "Updated memory." not in captured_contexts[1].messages[0].content


@pytest.mark.asyncio
async def test_session_custom_entry_accepts_json_like_payload() -> None:
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")

    entry_id = await session.append_custom_entry(
        "checkpoint",
        {"score": 1, "tags": ["typed", "json"], "ok": True},
    )

    entry = await session.get_entry(entry_id)
    assert entry is not None
    assert entry.data == {"score": 1, "tags": ["typed", "json"], "ok": True}
