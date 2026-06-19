"""Convenience assembly for the optional base tool bundle."""

from __future__ import annotations

from collections.abc import Mapping

from agent.types import AgentTool
from resources import ResourceResolver, ResourceStore
from runtime import ToolRegistry
from tools.ask_user import AskUserQuestionHandler, create_ask_user_question_tool
from tools.sleep import create_sleep_tool
from tools.skills import create_skills_tool
from tools.todo import create_todo_write_tool
from tools.web_fetch import WebFetcher, create_web_fetch_tool
from tools.web_search import SearchProviderRegistry, create_web_search_tool


def create_base_tool_registry(
    *,
    ask_user_handler: AskUserQuestionHandler | None = None,
    ask_user_timeout_seconds: float | None = None,
    sleep_max_seconds: float = 300,
    web_fetcher: WebFetcher | None = None,
    web_fetch_timeout_seconds: float = 20,
    web_fetch_max_bytes: int = 1_000_000,
    web_search_registry: SearchProviderRegistry | None = None,
    web_search_provider: str | None = None,
    web_search_env: Mapping[str, str] | None = None,
    skills_store: ResourceStore | None = None,
    skills_resolver: ResourceResolver | None = None,
) -> ToolRegistry:
    tools: list[AgentTool] = [
        create_sleep_tool(max_seconds=sleep_max_seconds),
        create_todo_write_tool(),
        create_ask_user_question_tool(
            handler=ask_user_handler,
            timeout_seconds=ask_user_timeout_seconds,
        ),
        create_web_fetch_tool(
            fetcher=web_fetcher,
            timeout_seconds=web_fetch_timeout_seconds,
            max_bytes=web_fetch_max_bytes,
        ),
        create_web_search_tool(
            registry=web_search_registry,
            provider=web_search_provider,
            env=web_search_env,
        ),
    ]
    if skills_store is not None:
        tools.append(create_skills_tool(skills_store, resolver=skills_resolver))
    return ToolRegistry(tools)
