"""Agent task adapter built on AgentRuntime and child harness sessions."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field

from agent_smith.core.agent.harness.types import AgentHarnessSession
from agent_smith.core.llm.types import AssistantMessage, JsonValue, TextContent
from agent_smith.core.runtime import AgentExecutionRequest, AgentExecutionResult, AgentRuntime
from agent_smith.core.tasks.types import TaskContext


class AgentTaskRunnerError(Exception):
    """Raised when an agent task cannot be started or completed."""


class AgentChildSessionRequest(BaseModel):
    task_id: str = Field(alias="taskId")
    agent_name: str = Field(alias="agentName")
    agent_depth: int = Field(alias="agentDepth")
    principal_id: str | None = Field(default=None, alias="principalId")
    parent_session_id: str | None = Field(default=None, alias="parentSessionId")
    parent_tool_call_id: str | None = Field(default=None, alias="parentToolCallId")
    parent_run_id: str | None = Field(default=None, alias="parentRunId")
    trace_id: str | None = Field(default=None, alias="traceId")
    description: str | None = None
    mode: Literal["sync", "async"] | None = None
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


AgentSessionFactory: TypeAlias = Callable[
    [AgentChildSessionRequest],
    AgentHarnessSession | Awaitable[AgentHarnessSession],
]


class AgentTaskResult(BaseModel):
    agent_name: str = Field(alias="agentName")
    status: Literal["completed"] = "completed"
    final_text: str = Field(alias="finalText")
    message_id: str | None = Field(default=None, alias="messageId")
    usage: dict[str, Any] | None = None
    run_id: str = Field(alias="runId")
    recording_status: str = Field(alias="recordingStatus")
    turns: int
    session_id: str = Field(alias="sessionId")

    model_config = {"populate_by_name": True}


class AgentTaskRunner:
    def __init__(
        self,
        *,
        agent_runtime: AgentRuntime,
        session_factory: AgentSessionFactory,
        max_depth: int = 3,
        abort_poll_seconds: float = 0.05,
    ) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be greater than or equal to 1")
        if abort_poll_seconds <= 0:
            raise ValueError("abort_poll_seconds must be greater than 0")
        self.agent_runtime = agent_runtime
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

        session_request = self._create_child_session_request(
            task_context=task_context,
            agent_name=agent_name,
            agent_depth=next_depth,
            parent_metadata=parent_metadata,
        )
        child_session = await self._create_child_session(session_request)
        child_metadata = await child_session.get_metadata()
        await task_context.set_result_metadata({"sessionId": child_metadata.id})
        is_background = session_request.mode == "async"
        await task_context.append_output(f"Started agent {agent_name}.\n")
        execution = await self._execute_with_abort(
            execution=self.agent_runtime.execute(
                AgentExecutionRequest(
                    session=child_session,
                    principal_id=child_metadata.principal_id,
                    agent_name=agent_name,
                    prompt=prompt,
                    flow="agent_task",
                    parent_run_id=session_request.parent_run_id,
                    correlation_id=task_context.task_id,
                    trace_id=session_request.trace_id,
                    is_background=is_background,
                    metadata={
                        "taskId": task_context.task_id,
                        "agentDepth": next_depth,
                        "parentToolCallId": session_request.parent_tool_call_id,
                        "mode": session_request.mode or "sync",
                    },
                )
            ),
            task_context=task_context,
        )
        response = execution.message
        final_text = _assistant_text(response)
        turns = await _count_assistant_turns(child_session)
        result = AgentTaskResult(
            agent_name=agent_name,
            final_text=final_text,
            message_id=None,
            usage=execution.usage.model_dump(mode="python", by_alias=True),
            runId=execution.run_id,
            recordingStatus=execution.recording_status,
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
                "runId": execution.run_id,
                "recordingStatus": execution.recording_status,
            }
        )
        await task_context.append_output(f"Completed agent {agent_name}.\n{final_text}\n")
        return result

    def _create_child_session_request(
        self,
        *,
        task_context: TaskContext,
        agent_name: str,
        agent_depth: int,
        parent_metadata: Mapping[str, Any] | None,
    ) -> AgentChildSessionRequest:
        provenance = _mapping_value(parent_metadata, "provenance")
        if provenance is not None and not isinstance(provenance, Mapping):
            raise AgentTaskRunnerError("parent_metadata.provenance must be a mapping")
        mode = _string_value(parent_metadata, "mode")
        if mode is not None and mode not in {"sync", "async"}:
            raise AgentTaskRunnerError("parent_metadata.mode must be sync or async")
        return AgentChildSessionRequest(
            task_id=task_context.task_id,
            agent_name=agent_name,
            agent_depth=agent_depth,
            principal_id=_string_value(parent_metadata, "principalId", "principal_id"),
            parent_session_id=_string_value(
                parent_metadata,
                "parentSessionId",
                "parent_session_id",
                "sessionId",
                "session_id",
            ),
            parent_tool_call_id=_string_value(
                parent_metadata,
                "parentToolCallId",
                "parent_tool_call_id",
            ),
            parent_run_id=_string_value(parent_metadata, "parentRunId", "parent_run_id"),
            trace_id=_string_value(parent_metadata, "traceId", "trace_id"),
            description=_string_value(parent_metadata, "description"),
            mode=mode,
            provenance=dict(provenance or {}),
        )

    async def _create_child_session(self, request: AgentChildSessionRequest) -> AgentHarnessSession:
        value = self.session_factory(request)
        if inspect.isawaitable(value):
            return await value
        return value

    async def _execute_with_abort(
        self,
        *,
        execution: Awaitable[AgentExecutionResult],
        task_context: TaskContext,
    ) -> AgentExecutionResult:
        if task_context.abort_signal.is_set():
            raise asyncio.CancelledError

        execution_task = asyncio.create_task(execution)
        try:
            while True:
                if task_context.abort_signal.is_set():
                    raise asyncio.CancelledError

                done, _ = await asyncio.wait({execution_task}, timeout=self.abort_poll_seconds)
                if done:
                    return execution_task.result()
        except asyncio.CancelledError:
            await self._cancel_execution(execution_task)
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

    async def _cancel_execution(self, execution_task: asyncio.Task[Any]) -> None:
        execution_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(execution_task, return_exceptions=True),
                timeout=1,
            )
        except TimeoutError:
            return


def _assistant_text(message: AssistantMessage) -> str:
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent)).strip()


async def _count_assistant_turns(session: AgentHarnessSession) -> int:
    entries = await session.get_entries()
    return sum(
        1
        for entry in entries
        if entry.type == "message" and entry.message is not None and entry.message.role == "assistant"
    )


def _mapping_value(metadata: Mapping[str, Any] | None, *keys: str) -> Any:
    if not metadata:
        return None
    for key in keys:
        if key in metadata:
            return metadata[key]
    return None


def _string_value(metadata: Mapping[str, Any] | None, *keys: str) -> str | None:
    value = _mapping_value(metadata, *keys)
    if value is None:
        return None
    return str(value)
