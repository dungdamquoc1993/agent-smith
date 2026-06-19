"""Agent task runner built on AgentFactory and child harness sessions."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field

from agent.harness.types import AgentHarnessSession
from ai.types import AssistantMessage, TextContent
from runtime import AgentFactory
from tasks.types import TaskContext

AgentSessionFactory: TypeAlias = Callable[
    [str],
    AgentHarnessSession | Awaitable[AgentHarnessSession],
]


class AgentTaskRunnerError(Exception):
    """Raised when an agent task cannot be started or completed."""


class AgentTaskResult(BaseModel):
    agent_name: str = Field(alias="agentName")
    status: Literal["completed"] = "completed"
    final_text: str = Field(alias="finalText")
    message_id: str | None = Field(default=None, alias="messageId")
    usage: dict[str, Any] | None = None
    turns: int
    session_id: str = Field(alias="sessionId")

    model_config = {"populate_by_name": True}


class AgentTaskRunner:
    def __init__(
        self,
        *,
        agent_factory: AgentFactory,
        session_factory: AgentSessionFactory,
        max_depth: int = 3,
        abort_poll_seconds: float = 0.05,
    ) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be greater than or equal to 1")
        if abort_poll_seconds <= 0:
            raise ValueError("abort_poll_seconds must be greater than 0")
        self.agent_factory = agent_factory
        self.session_factory = session_factory
        self.max_depth = max_depth
        self.abort_poll_seconds = abort_poll_seconds

    async def run(
        self,
        *,
        task_context: TaskContext,
        agent_name: str,
        prompt: str,
        parent_metadata: Mapping[str, Any] | None = None,
    ) -> AgentTaskResult:
        depth = self._resolve_depth(parent_metadata)
        if depth >= self.max_depth:
            raise AgentTaskRunnerError(
                f"Agent task depth {depth} reached max depth {self.max_depth}"
            )
        next_depth = depth + 1
        await task_context.set_result_metadata(
            {
                "agentName": agent_name,
                "agentDepth": next_depth,
            }
        )

        child_session = await self._create_child_session(task_context.task_id)
        child_metadata = await child_session.get_metadata()
        await task_context.set_result_metadata({"sessionId": child_metadata.id})
        harness = await self.agent_factory.create_harness(agent_name, session=child_session)

        await task_context.append_output(f"Started agent {agent_name}.\n")
        response = await self._prompt_with_abort(
            harness=harness,
            prompt=prompt,
            task_context=task_context,
        )
        final_text = _assistant_text(response)
        turns = await _count_assistant_turns(child_session)
        result = AgentTaskResult(
            agent_name=agent_name,
            final_text=final_text,
            message_id=None,
            usage=response.usage.model_dump(mode="python", by_alias=True)
            if response.usage
            else None,
            turns=turns,
            session_id=child_metadata.id,
        )
        await task_context.set_result_metadata(
            {
                "agentName": agent_name,
                "agentDepth": next_depth,
                "sessionId": child_metadata.id,
                "stopReason": response.stop_reason,
                "turns": turns,
            }
        )
        await task_context.append_output(f"Completed agent {agent_name}.\n{final_text}\n")
        return result

    async def _create_child_session(self, task_id: str) -> AgentHarnessSession:
        value = self.session_factory(task_id)
        if inspect.isawaitable(value):
            return await value
        return value

    async def _prompt_with_abort(
        self,
        *,
        harness: Any,
        prompt: str,
        task_context: TaskContext,
    ) -> AssistantMessage:
        if task_context.abort_signal.is_set():
            raise asyncio.CancelledError

        prompt_task = asyncio.create_task(harness.prompt(prompt))
        try:
            while True:
                if task_context.abort_signal.is_set():
                    await self._abort_harness(harness, prompt_task)
                    raise asyncio.CancelledError

                done, _ = await asyncio.wait({prompt_task}, timeout=self.abort_poll_seconds)
                if done:
                    return prompt_task.result()
        except asyncio.CancelledError:
            await self._abort_harness(harness, prompt_task)
            raise

    def _resolve_depth(self, parent_metadata: Mapping[str, Any] | None) -> int:
        if not parent_metadata:
            return 0
        raw_depth = parent_metadata.get("agentDepth", parent_metadata.get("agent_depth", 0))
        try:
            depth = int(raw_depth)
        except (TypeError, ValueError) as exc:
            raise AgentTaskRunnerError("parent_metadata.agentDepth must be an integer") from exc
        if depth < 0:
            raise AgentTaskRunnerError("parent_metadata.agentDepth must be greater than or equal to 0")
        return depth

    async def _abort_harness(self, harness: Any, prompt_task: asyncio.Task[Any]) -> None:
        abort_task = asyncio.create_task(harness.abort())
        prompt_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(prompt_task, abort_task, return_exceptions=True),
                timeout=1,
            )
        except TimeoutError:
            abort_task.cancel()
            await asyncio.gather(abort_task, return_exceptions=True)


def _assistant_text(message: AssistantMessage) -> str:
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent)).strip()


async def _count_assistant_turns(session: AgentHarnessSession) -> int:
    entries = await session.get_entries()
    return sum(
        1
        for entry in entries
        if entry.type == "message" and entry.message is not None and entry.message.role == "assistant"
    )
