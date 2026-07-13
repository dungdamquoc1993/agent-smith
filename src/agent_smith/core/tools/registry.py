"""Convenience assembly for the optional base tool bundle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agent_smith.core.agent.types import AgentTool
from agent_smith.core.permissions.host import ToolApprovalHandler, create_can_use_tool
from agent_smith.core.permissions.types import CanUseTool
from agent_smith.core.resources import ResourceResolver, ResourceStore
from agent_smith.core.runtime import ToolRegistry
from agent_smith.core.tasks import AgentTaskRunner, TaskRuntime
from agent_smith.core.tools.ask_user import AskUserQuestionHandler, create_ask_user_question_tool
from agent_smith.core.tools.bio import create_bio_tool
from agent_smith.core.tools.cronjob import create_cronjob_tool
from agent_smith.core.tools.heartbeat import create_heartbeat_tool
from agent_smith.core.tools.manage_resources import create_manage_resources_tool
from agent_smith.core.tools.personal_context import create_personal_context_tool
from agent_smith.core.tools.skill import create_skill_tool
from agent_smith.core.tools.sleep import create_sleep_tool
from agent_smith.core.tools.task import create_task_tool
from agent_smith.core.tools.task_output import create_task_output_tool
from agent_smith.core.tools.task_stop import create_task_stop_tool
from agent_smith.core.tools.todo import create_todo_write_tool
from agent_smith.core.tools.web_fetch import WebFetcher, create_web_fetch_tool
from agent_smith.core.tools.web_search import SearchProviderRegistry, create_web_search_tool


def create_base_can_use_tool(
    *,
    ask_user_handler: AskUserQuestionHandler | None = None,
    tool_approval_handler: ToolApprovalHandler | None = None,
) -> CanUseTool | None:
    return create_can_use_tool(
        ask_user_handler=ask_user_handler,
        tool_approval_handler=tool_approval_handler,
    )


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
    resources_store: ResourceStore | None = None,
    resources_resolver: ResourceResolver | None = None,
    task_runtime: TaskRuntime | None = None,
    agent_runner: AgentTaskRunner | None = None,
    agent_parent_metadata: Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None = None,
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
        create_personal_context_tool(),
        create_bio_tool(),
        create_heartbeat_tool(),
        create_cronjob_tool(),
    ]
    if resources_resolver is not None:
        tools.append(create_skill_tool(resolver=resources_resolver))
    if resources_store is not None:
        tools.append(
            create_manage_resources_tool(
                resources_store,
                resolver=resources_resolver,
            )
        )
    if task_runtime is not None and agent_runner is not None:
        tools.append(
            create_task_tool(
                task_runtime,
                agent_runner,
                parent_metadata=agent_parent_metadata,
            )
        )
    if task_runtime is not None:
        tools.extend(
            [
                create_task_output_tool(task_runtime),
                create_task_stop_tool(task_runtime),
            ]
        )
    return ToolRegistry(tools)
