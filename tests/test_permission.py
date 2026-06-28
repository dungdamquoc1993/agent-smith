from __future__ import annotations

import pytest

from agent import AgentContext, AgentLoopConfig, AgentTool, AgentToolResult, agent_loop
from agent.harness import AgentHarness, MemorySessionRepo
from ai.events import create_assistant_message_event_stream
from ai.models import make_litellm_model
from ai.types import (
    AssistantMessage,
    AssistantMessageEventDone,
    AssistantMessageEventStart,
    AssistantMessageEventToolcallEnd,
    AssistantMessageEventToolcallStart,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    UserMessage,
)
from permission import (
    InMemoryPermissionRuleStore,
    PermissionDecision,
    PermissionRequest,
    PermissionResolver,
    PermissionRule,
    ToolPermissionSpec,
    rule_provider_from_store,
)
from permission.host import create_can_use_tool
from permission.tool_specs import MUTATING_ASK, READ_ONLY_ALLOW


def _now() -> int:
    return 1700000000000


def _model() -> Model:
    return make_litellm_model(provider="openai", model_id="gpt-test")


def _user(text: str = "hello") -> UserMessage:
    return UserMessage(content=text, timestamp=_now())


def _assistant(content: list, stop_reason: str = "stop") -> AssistantMessage:
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
            if isinstance(block, ToolCall):
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
async def test_permission_resolver_hard_deny() -> None:
    resolver = PermissionResolver(hard_deny=["task"])
    decision = await resolver.resolve(
        PermissionRequest(
            tool_name="task",
            tool_call_id="tc-1",
            input={"agent_name": "worker", "description": "x", "prompt": "y"},
            tool_spec=MUTATING_ASK,
        )
    )
    assert decision.behavior == "deny"
    assert decision.source == "hard_deny"


@pytest.mark.asyncio
async def test_permission_resolver_scope_precedence() -> None:
    store = InMemoryPermissionRuleStore(
        [
            PermissionRule(pattern="*", behavior="allow", scope="user"),
            PermissionRule(pattern="web_fetch", behavior="allow", scope="session"),
        ]
    )
    resolver = PermissionResolver(rule_provider=rule_provider_from_store(store))
    decision = await resolver.resolve(
        PermissionRequest(
            tool_name="web_fetch",
            tool_call_id="tc-1",
            input={"url": "https://example.com"},
            tool_spec=READ_ONLY_ALLOW,
        )
    )
    assert decision.behavior == "allow"
    assert decision.source == "rule:session"


@pytest.mark.asyncio
async def test_permission_resolver_plan_mode_blocks_mutating_tool() -> None:
    resolver = PermissionResolver()
    decision = await resolver.resolve(
        PermissionRequest(
            tool_name="manage_resources",
            tool_call_id="tc-1",
            input={"kind": "skill", "action": "create", "name": "demo", "content": {}},
            mode="plan",
            tool_spec=MUTATING_ASK,
        )
    )
    assert decision.behavior == "deny"
    assert decision.source == "mode:plan"


@pytest.mark.asyncio
async def test_permission_resolver_accept_edits_allows_mutating_tool() -> None:
    resolver = PermissionResolver()
    decision = await resolver.resolve(
        PermissionRequest(
            tool_name="manage_resources",
            tool_call_id="tc-1",
            input={"kind": "skill", "action": "create", "name": "demo", "content": {}},
            mode="accept_edits",
            tool_spec=MUTATING_ASK,
        )
    )
    assert decision.behavior == "allow"
    assert decision.source == "mode:accept_edits"


@pytest.mark.asyncio
async def test_permission_resolver_background_ask_becomes_deny_in_harness() -> None:
    from permission.harness import resolve_harness_tool_permission

    tool = AgentTool(
        name="task",
        label="Task",
        description="task",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
        execute=lambda *_args, **_kwargs: AgentToolResult(content=[TextContent(text="ok")]),
        permission=MUTATING_ASK,
    )
    resolver = PermissionResolver()
    decision = await resolve_harness_tool_permission(
        tool=tool,
        tool_call_id="tc-1",
        args={"agent_name": "worker", "description": "x", "prompt": "y"},
        permission_mode="default",
        is_background=True,
        permission_resolver=resolver,
        can_use_tool=None,
    )
    assert decision is not None
    assert decision.behavior == "deny"
    assert decision.source == "headless"


@pytest.mark.asyncio
async def test_agent_loop_applies_updated_args_from_before_tool_call() -> None:
    captured: dict[str, object] = {}

    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        captured["args"] = args
        return AgentToolResult(content=[TextContent(text="done")])

    tool = AgentTool(
        name="demo_tool",
        label="Demo",
        description="demo",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execute=execute,
        permission=READ_ONLY_ALLOW,
    )

    async def before_tool_call(context, signal=None):
        _ = context, signal
        return {"updatedArgs": {"value": "patched"}}

    assistant = _assistant(
        [ToolCall(id="tc-1", name="demo_tool", arguments={"value": "original"})],
        stop_reason="toolUse",
    )
    final = _assistant([TextContent(text="thanks")], stop_reason="stop")

    async def stream_fn(model, context, options=None):
        _ = model, context, options
        if len(context.messages) == 1:
            return _stream_for(assistant)
        return _stream_for(final)

    stream = agent_loop(
        [_user()],
        AgentContext(tools=[tool]),
        AgentLoopConfig(model=_model(), before_tool_call=before_tool_call),
        stream_fn=stream_fn,
    )
    messages = await stream.result()
    assert captured["args"] == {"value": "patched"}
    assert any(message.role == "toolResult" for message in messages)


@pytest.mark.asyncio
async def test_harness_permission_denies_mutating_tool_in_plan_mode() -> None:
    executed = False

    async def execute(tool_call_id, args, signal=None, on_update=None):
        nonlocal executed
        _ = tool_call_id, args, signal, on_update
        executed = True
        return AgentToolResult(content=[TextContent(text="done")])

    tool = AgentTool(
        name="manage_resources",
        label="Manage Resources",
        description="manage",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "action": {"type": "string"},
                "name": {"type": "string"},
                "content": {"type": "object"},
            },
            "required": ["kind", "action"],
            "additionalProperties": True,
        },
        execute=execute,
        permission=MUTATING_ASK,
    )
    store = InMemoryPermissionRuleStore()
    resolver = PermissionResolver(rule_provider=rule_provider_from_store(store))
    repo = MemorySessionRepo()
    session = await repo.create(principal_id="principal-1")
    harness = AgentHarness(
        session=session,
        model=_model(),
        tools=[tool],
        active_tool_names=[tool.name],
        permission_mode="plan",
        permission_resolver=resolver,
        permission_rule_store=store,
    )

    assistant = _assistant(
        [
            ToolCall(
                id="tc-1",
                name="manage_resources",
                arguments={
                    "kind": "skill",
                    "action": "create",
                    "name": "demo",
                    "content": {"content": "x"},
                },
            )
        ],
        stop_reason="toolUse",
    )
    final = _assistant([TextContent(text="ok")], stop_reason="stop")

    async def stream_fn(model, context, options=None):
        _ = model, options
        if len(context.messages) == 1:
            return _stream_for(assistant)
        return _stream_for(final)

    harness.stream_fn = stream_fn
    await harness.prompt("run tool")
    assert executed is False


@pytest.mark.asyncio
async def test_can_use_tool_persists_session_rule_on_approval() -> None:
    store = InMemoryPermissionRuleStore()

    async def approve(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow(
            source="user",
            persist_rule=PermissionRule(
                pattern=request.tool_name,
                behavior="allow",
                scope="session",
            ),
        )

    can_use_tool = create_can_use_tool(tool_approval_handler=approve)
    assert can_use_tool is not None
    resolver = PermissionResolver(rule_provider=rule_provider_from_store(store))
    decision = await resolver.resolve(
        PermissionRequest(
            tool_name="task",
            tool_call_id="tc-1",
            input={"agent_name": "worker", "description": "x", "prompt": "y"},
            tool_spec=MUTATING_ASK,
        )
    )
    assert decision.behavior == "ask"

    from permission.harness import resolve_harness_tool_permission

    tool = AgentTool(
        name="task",
        label="Task",
        description="task",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
        execute=lambda *_args, **_kwargs: AgentToolResult(content=[TextContent(text="ok")]),
        permission=MUTATING_ASK,
    )
    approved = await resolve_harness_tool_permission(
        tool=tool,
        tool_call_id="tc-1",
        args={"agent_name": "worker", "description": "x", "prompt": "y"},
        permission_mode="default",
        is_background=False,
        permission_resolver=resolver,
        can_use_tool=can_use_tool,
        permission_rule_store=store,
    )
    assert approved is not None
    assert approved.behavior == "allow"

    follow_up = await resolver.resolve(
        PermissionRequest(
            tool_name="task",
            tool_call_id="tc-2",
            input={"agent_name": "worker", "description": "x", "prompt": "y"},
            tool_spec=MUTATING_ASK,
        )
    )
    assert follow_up.behavior == "allow"
    assert follow_up.source == "rule:session"
