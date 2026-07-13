from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agent_smith.core.agent import AgentContext, AgentLoopConfig, AgentToolResult, agent_loop
from agent_smith.core.agent.validation import validate_tool_arguments
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
    Model,
    SimpleStreamOptions,
    TextContent,
    ToolCall,
    UserMessage,
)
from agent_smith.core.tools import (
    BIO_TOOL_NAME,
    BraveSearchProvider,
    CRONJOB_TOOL_NAME,
    HEARTBEAT_TOOL_NAME,
    PERSONAL_CONTEXT_TOOL_NAME,
    SearchProviderRegistry,
    SearchRequest,
    SearchResult,
    TavilySearchProvider,
    create_ask_user_question_tool,
    create_base_tool_registry,
    create_bio_tool,
    create_cronjob_tool,
    create_heartbeat_tool,
    create_personal_context_tool,
    create_sleep_tool,
    create_todo_write_tool,
    create_web_fetch_tool,
    create_web_search_tool,
)
from agent_smith.core.resources import ResourceResolver
from helpers.resource_stores import MemoryResourceStore


def _now() -> int:
    return int(time.time() * 1000)


def _model() -> Model:
    return make_litellm_model(provider="openai", model_id="gpt-test")


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


class AbortFlag:
    def __init__(self) -> None:
        self.aborted = False

    def is_set(self) -> bool:
        return self.aborted


@pytest.mark.asyncio
async def test_sleep_tool_waits_caps_and_aborts() -> None:
    tool = create_sleep_tool(max_seconds=0.2)

    validate_tool_arguments(
        tool,
        ToolCall(id="sleep-1", name="sleep", arguments={"seconds": 0.01}),
    )
    result = await tool.execute("sleep-1", {"seconds": 0.01}, None, None)
    assert isinstance(result, AgentToolResult)
    assert result.details["seconds"] == 0.01

    with pytest.raises(ValueError, match="less than or equal"):
        await tool.execute("sleep-2", {"seconds": 1}, None, None)

    flag = AbortFlag()
    task = asyncio.create_task(tool.execute("sleep-3", {"seconds": 0.2}, flag, None))
    await asyncio.sleep(0.02)
    flag.aborted = True
    with pytest.raises(RuntimeError, match="aborted"):
        await task


@pytest.mark.asyncio
async def test_todo_write_is_stateless_full_list_echo() -> None:
    tool = create_todo_write_tool()
    payload = {
        "todos": [
            {"id": "1", "content": "Sketch API", "status": "completed"},
            {"content": "Write tests", "status": "in_progress"},
            {"content": "Ship", "status": "pending"},
        ]
    }

    validate_tool_arguments(tool, ToolCall(id="todo-1", name="todo_write", arguments=payload))
    first = await tool.execute("todo-1", payload, None, None)
    second = await tool.execute("todo-2", {"todos": []}, None, None)

    assert first.details["todos"] == payload["todos"]
    assert first.details["counts"] == {"pending": 1, "inProgress": 1, "completed": 1}
    assert second.details["todos"] == []


@pytest.mark.asyncio
async def test_ask_user_question_tool_waits_for_handler_response() -> None:
    async def handler(request, signal):
        assert request.tool_call_id == "ask-1"
        assert signal is None
        return {"answers": {request.questions[0].question: "Use callbacks"}}

    tool = create_ask_user_question_tool(handler)
    payload = {
        "questions": [
            {
                "question": "Which path should we take?",
                "header": "Path",
                "options": [
                    {"label": "A", "description": "First path"},
                    {"label": "B", "description": "Second path"},
                ],
            }
        ]
    }

    validate_tool_arguments(
        tool,
        ToolCall(id="ask-1", name="ask_user_question", arguments=payload),
    )
    result = await tool.execute("ask-1", payload, None, None)

    assert result.details["answers"] == {"Which path should we take?": "Use callbacks"}
    assert "Use callbacks" in result.content[0].text


@pytest.mark.asyncio
async def test_ask_user_question_missing_handler_errors() -> None:
    tool = create_ask_user_question_tool()
    with pytest.raises(RuntimeError, match="not configured"):
        await tool.execute("ask-1", {"questions": []}, None, None)


@pytest.mark.asyncio
async def test_ask_user_question_integration_pauses_and_resumes_agent_loop() -> None:
    started = asyncio.Event()
    answer: asyncio.Future[dict[str, Any]] = asyncio.Future()

    async def handler(request, signal):
        _ = request, signal
        started.set()
        return await answer

    tool = create_ask_user_question_tool(handler)
    first = _assistant(
        [
            ToolCall(
                id="ask-call",
                name="ask_user_question",
                arguments={
                    "questions": [
                        {
                            "question": "Proceed?",
                            "header": "Next",
                            "options": [
                                {"label": "Yes", "description": "Continue"},
                                {"label": "No", "description": "Stop"},
                            ],
                        }
                    ]
                },
            )
        ],
        stop_reason="toolUse",
    )
    second = _assistant([TextContent(text="continuing")])
    calls = 0

    def stream_fn(model: Model, context: Context, options: SimpleStreamOptions | None = None):
        nonlocal calls
        _ = model, context, options
        calls += 1
        return _stream_for(first if calls == 1 else second)

    stream = agent_loop(
        [_user()],
        AgentContext(messages=[], tools=[tool]),
        AgentLoopConfig(model=_model()),
        stream_fn=stream_fn,
    )

    async def collect():
        events = [event async for event in stream]
        return events, await stream.result()

    task = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), timeout=1)
    assert not task.done()

    answer.set_result({"answers": {"Proceed?": "Yes"}})
    events, result = await task

    assert calls == 2
    assert [message.role for message in result] == ["user", "assistant", "toolResult", "assistant"]
    assert result[-1].content[0].text == "continuing"
    assert "tool_execution_start" in [event.type for event in events]


@pytest.mark.asyncio
async def test_web_fetch_extracts_html_and_reports_truncation() -> None:
    async def fetcher(url: str, timeout_seconds: float, max_bytes: int):
        assert url == "https://example.com"
        assert timeout_seconds == 7
        assert max_bytes == 1000
        return {
            "url": url,
            "finalUrl": "https://example.com/final",
            "status": 200,
            "reason": "OK",
            "contentType": "text/html; charset=utf-8",
            "body": b"<html><body>Hello <b>world</b></body></html>",
        }

    tool = create_web_fetch_tool(fetcher=fetcher, timeout_seconds=7, max_bytes=1000)
    result = await tool.execute(
        "fetch-1",
        {"url": "https://example.com", "max_chars": 5},
        None,
        None,
    )

    assert result.details["finalUrl"] == "https://example.com/final"
    assert result.details["bytes"] > 5
    assert result.details["truncated"] is True
    assert "Hello" in result.content[0].text


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_http_urls() -> None:
    tool = create_web_fetch_tool(fetcher=lambda url, timeout, max_bytes: {})
    with pytest.raises(ValueError, match="http or https"):
        await tool.execute("fetch-1", {"url": "file:///etc/passwd"}, None, None)


class FakeSearchProvider:
    name = "fake"
    required_env = ("FAKE_SEARCH_API_KEY",)

    def is_configured(self, env):
        return bool(env.get("FAKE_SEARCH_API_KEY"))

    async def search(self, request: SearchRequest, env) -> list[SearchResult]:
        assert env["FAKE_SEARCH_API_KEY"] == "secret"
        assert request.query == "agent tools"
        return [
            SearchResult(title="Allowed", url="https://docs.example.com/a", snippet="ok"),
            SearchResult(title="Blocked", url="https://blocked.example.com/b", snippet="bad"),
        ]


@pytest.mark.asyncio
async def test_web_search_selects_configured_provider_and_filters_domains() -> None:
    registry = SearchProviderRegistry([FakeSearchProvider()])
    tool = create_web_search_tool(registry=registry, env={"FAKE_SEARCH_API_KEY": "secret"})

    result = await tool.execute(
        "search-1",
        {
            "query": "agent tools",
            "allowed_domains": ["example.com"],
            "blocked_domains": ["blocked.example.com"],
        },
        None,
        None,
    )

    assert result.details["provider"] == "fake"
    assert result.details["results"] == [
        {"title": "Allowed", "url": "https://docs.example.com/a", "snippet": "ok"}
    ]
    assert "Allowed" in result.content[0].text
    assert "Blocked" not in result.content[0].text


@pytest.mark.asyncio
async def test_web_search_errors_when_no_provider_has_credentials() -> None:
    registry = SearchProviderRegistry([FakeSearchProvider()])
    tool = create_web_search_tool(registry=registry, env={})

    with pytest.raises(RuntimeError, match="No configured web search provider"):
        await tool.execute("search-1", {"query": "agent tools"}, None, None)


@pytest.mark.asyncio
async def test_web_search_tavily_provider_normalizes_mocked_response() -> None:
    async def post_json(url, headers, payload, timeout_seconds):
        assert url == "https://api.tavily.com/search"
        assert headers["Authorization"] == "Bearer tvly-test"
        assert payload["query"] == "agent tools"
        assert timeout_seconds == 20
        return {
            "results": [
                {
                    "title": "Tavily result",
                    "url": "https://example.com/tavily",
                    "content": "summary",
                }
            ]
        }

    provider = TavilySearchProvider(post_json=post_json)
    registry = SearchProviderRegistry([provider])
    tool = create_web_search_tool(
        registry=registry,
        provider="tavily",
        env={"TAVILY_API_KEY": "tvly-test"},
    )

    result = await tool.execute("search-1", {"query": "agent tools"}, None, None)

    assert result.details["provider"] == "tavily"
    assert result.details["results"][0]["title"] == "Tavily result"
    assert result.details["results"][0]["snippet"] == "summary"


@pytest.mark.asyncio
async def test_web_search_brave_provider_uses_env_selector_and_normalizes_response() -> None:
    async def get_json(url, headers, timeout_seconds):
        assert url.startswith("https://api.search.brave.com/res/v1/web/search?")
        assert headers["X-Subscription-Token"] == "brave-test"
        assert timeout_seconds == 20
        return {
            "web": {
                "results": [
                    {
                        "title": "Brave result",
                        "url": "https://example.com/brave",
                        "description": "description",
                    }
                ]
            }
        }

    provider = BraveSearchProvider(get_json=get_json)
    registry = SearchProviderRegistry([provider])
    tool = create_web_search_tool(
        registry=registry,
        env={
            "AGENT_SMITH_WEB_SEARCH_PROVIDER": "brave",
            "BRAVE_SEARCH_API_KEY": "brave-test",
        },
    )

    result = await tool.execute("search-1", {"query": "agent tools"}, None, None)

    assert result.details["provider"] == "brave"
    assert result.details["results"][0]["title"] == "Brave result"
    assert result.details["results"][0]["snippet"] == "description"


def test_base_tool_registry_contains_phase_1_tools() -> None:
    registry = create_base_tool_registry(web_search_env={})
    assert registry.names() == [
        "sleep",
        "todo_write",
        "ask_user_question",
        "web_fetch",
        "web_search",
        "personal_context",
        "bio",
        "heartbeat",
        "cronjob",
    ]


def test_base_tool_registry_optionally_includes_resource_tools() -> None:
    store = MemoryResourceStore()
    registry = create_base_tool_registry(
        web_search_env={},
        resources_store=store,
        resources_resolver=ResourceResolver([store]),
    )

    assert registry.names() == [
        "sleep",
        "todo_write",
        "ask_user_question",
        "web_fetch",
        "web_search",
        "personal_context",
        "bio",
        "heartbeat",
        "cronjob",
        "skill",
        "manage_resources",
    ]


@pytest.mark.asyncio
async def test_personal_context_and_bio_tool_interfaces_are_stubbed() -> None:
    personal_context = create_personal_context_tool()
    bio = create_bio_tool()

    assert personal_context.name == PERSONAL_CONTEXT_TOOL_NAME
    assert personal_context.label == "personal_context"
    assert personal_context.permission.read_only is True
    assert personal_context.parameters["required"] == ["action"]
    assert personal_context.parameters["properties"]["action"]["enum"] == ["search", "get"]

    search_result = await personal_context.execute(
        "context-1",
        {
            "action": "search",
            "query": "Find previous conversations about Agent Smith context framing.",
            "sources": ["conversations"],
            "limit": 5,
        },
        None,
        None,
    )
    assert search_result.details["implemented"] is False
    assert search_result.details["action"] == "search"
    assert "not implemented yet" in search_result.content[0].text

    get_result = await personal_context.execute(
        "context-2",
        {
            "action": "get",
            "id": "ctx-123",
            "sources": ["user_knowledge_memory"],
        },
        None,
        None,
    )
    assert get_result.details["implemented"] is False
    assert get_result.details["action"] == "get"
    assert get_result.details["id"] == "ctx-123"
    assert "not implemented yet" in get_result.content[0].text

    assert bio.name == BIO_TOOL_NAME
    assert bio.label == "bio"
    assert bio.permission.default == "ask"
    assert bio.permission.mutates_files is True
    assert bio.parameters["properties"]["action"]["enum"] == ["add", "update", "forget"]

    bio_result = await bio.execute(
        "bio-1",
        {
            "action": "add",
            "request": "Remember that the user is designing Agent Smith context frames.",
            "section_hint": "Project Goal",
        },
        None,
        None,
    )
    assert bio_result.details["implemented"] is False
    assert "was not changed" in bio_result.content[0].text


@pytest.mark.asyncio
async def test_scheduled_tool_interfaces_are_stubbed() -> None:
    heartbeat = create_heartbeat_tool()
    cronjob = create_cronjob_tool()

    assert heartbeat.name == HEARTBEAT_TOOL_NAME
    assert heartbeat.label == "Heartbeat"
    assert heartbeat.permission.default == "ask"
    assert heartbeat.parameters["required"] == ["interval_seconds", "description", "prompt"]
    assert heartbeat.parameters["properties"]["execution_model"]["enum"] == [
        "wake_agent",
        "system_job",
        "either",
        "undecided",
    ]

    heartbeat_result = await heartbeat.execute(
        "heartbeat-1",
        {
            "interval_seconds": 300,
            "description": "Periodic check for pending follow-up work.",
            "prompt": "Check whether there is follow-up work to prepare.",
            "execution_model": "undecided",
        },
        None,
        None,
    )
    assert heartbeat_result.details["implemented"] is False
    assert heartbeat_result.details["design"]["scopeDecision"] == "open"
    assert "no timer" in heartbeat_result.content[0].text

    assert cronjob.name == CRONJOB_TOOL_NAME
    assert cronjob.label == "Cronjob"
    assert cronjob.permission.default == "ask"
    assert cronjob.parameters["required"] == ["run_at", "description", "prompt"]
    assert cronjob.parameters["properties"]["execution_model"]["enum"] == [
        "wake_agent",
        "system_job",
        "either",
        "undecided",
    ]

    cronjob_result = await cronjob.execute(
        "cronjob-1",
        {
            "run_at": "2026-07-09T09:00:00+07:00",
            "timezone": "Asia/Ho_Chi_Minh",
            "description": "Run a fixed-time reminder.",
            "prompt": "Prepare a fixed-time reminder.",
        },
        None,
        None,
    )
    assert cronjob_result.details["implemented"] is False
    assert cronjob_result.details["design"]["scopeDecision"] == "open"
    assert "no scheduler entry" in cronjob_result.content[0].text
