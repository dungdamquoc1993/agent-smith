"""Convenience assembly for the optional base tool bundle."""

from __future__ import annotations

from collections.abc import Mapping

from agent.types import AgentTool
from resources import ResourceResolver, ResourceStore
from runtime import ToolRegistry
from tasks import AgentTaskRunner, TaskRuntime
from tools.agent import create_agent_tool
from tools.agents import create_agents_tool
from tools.ask_user import AskUserQuestionHandler, create_ask_user_question_tool
from tools.sleep import create_sleep_tool
from tools.skills import create_skills_tool
from tools.task_output import create_task_output_tool
from tools.task_stop import create_task_stop_tool
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
    agents_store: ResourceStore | None = None,
    agents_resolver: ResourceResolver | None = None,
    task_runtime: TaskRuntime | None = None,
    agent_runner: AgentTaskRunner | None = None,
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
    if agents_store is not None:
        tools.append(create_agents_tool(agents_store, resolver=agents_resolver))
    if task_runtime is not None and agent_runner is not None:
        tools.append(create_agent_tool(task_runtime, agent_runner))
    if task_runtime is not None:
        tools.extend(
            [
                create_task_output_tool(task_runtime),
                create_task_stop_tool(task_runtime),
            ]
        )
    return ToolRegistry(tools)
