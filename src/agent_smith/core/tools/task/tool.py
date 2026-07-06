"""Agent task spawning tool factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from typing import Literal

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentTool
from agent_smith.core.permissions.tool_specs import TASK_ASK
from agent_smith.core.tasks import AgentTaskRunner, TaskRuntime, TaskTimeoutError
from agent_smith.core.tools.shared.common import is_aborted, text_result
from agent_smith.core.tools.shared.task_serialization import (
    serialize_value,
    task_output_to_details,
    task_record_to_details,
    task_result_text,
)
from agent_smith.core.tools.task.constants import TASK_TOOL_NAME

TaskToolMode = Literal["sync", "async"]
TaskToolParentMetadata = Mapping[str, Any] | Callable[[], Mapping[str, Any]]


class TaskToolInput(BaseModel):
    agent_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    mode: TaskToolMode | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)


def create_task_tool(
    task_runtime: TaskRuntime,
    agent_runner: AgentTaskRunner,
    *,
    default_mode: TaskToolMode = "sync",
    sync_timeout_seconds: float | None = None,
    parent_metadata: TaskToolParentMetadata | None = None,
) -> AgentTool:
    if default_mode not in {"sync", "async"}:
        raise ValueError("default_mode must be sync or async")

    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = on_update
        payload = TaskToolInput.model_validate(args)
        mode = payload.mode or default_mode
        resolved_parent_metadata = _resolve_parent_metadata(parent_metadata)
        parent_provenance = resolved_parent_metadata.get("provenance")
        provenance = dict(parent_provenance) if isinstance(parent_provenance, Mapping) else {}
        provenance.update({"trigger": "task_tool", "mode": mode})
        depth = resolved_parent_metadata.get(
            "agentDepth",
            resolved_parent_metadata.get("agent_depth", 0),
        )
        metadata = {
            **resolved_parent_metadata,
            "agentName": payload.agent_name,
            "agentDepth": depth,
            "mode": mode,
            "description": payload.description,
            "parentToolCallId": tool_call_id,
            "provenance": provenance,
        }
        spawned = await task_runtime.spawn(
            kind="agent",
            description=payload.description,
            metadata=metadata,
            run=lambda context: agent_runner.run(
                task_context=context,
                agent_name=payload.agent_name,
                prompt=payload.prompt,
                parent_metadata=metadata,
            ),
        )

        if mode == "async":
            return text_result(
                f"Launched agent task {spawned.id} for {payload.agent_name}.",
                details={
                    "status": "launched",
                    "taskId": spawned.id,
                    "agentName": payload.agent_name,
                    "description": payload.description,
                    "outputPath": spawned.output_path,
                    "task": task_record_to_details(spawned),
                },
            )

        timeout_seconds = payload.timeout_seconds or sync_timeout_seconds
        try:
            while True:
                if is_aborted(signal):
                    await task_runtime.stop(spawned.id, reason="Parent tool call aborted")
                    raise RuntimeError("Operation aborted")
                try:
                    record = await task_runtime.wait(spawned.id, timeout_seconds=0.05)
                    break
                except TaskTimeoutError:
                    if timeout_seconds is not None:
                        timeout_seconds -= 0.05
                        if timeout_seconds <= 0:
                            raise TimeoutError(
                                f"Timed out waiting for agent task {spawned.id}"
                            ) from None
        except Exception:
            if is_aborted(signal):
                raise
            raise

        output = await task_runtime.read_output(spawned.id)
        details = {
            "taskId": spawned.id,
            "status": record.status,
            "agentName": payload.agent_name,
            "result": serialize_value(record.result),
            "error": serialize_value(record.error),
            "output": task_output_to_details(output),
            "task": task_record_to_details(record),
        }
        text = task_result_text(record) or f"Agent task {spawned.id} finished with {record.status}."
        return text_result(text, details=details)

    return AgentTool(
        name=TASK_TOOL_NAME,
        label="Task",
        description=(
            "Run a named agent task, either synchronously or in the background. "
            "Available agent definitions are listed in system-reminder messages in the conversation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Agent definition name to run.",
                },
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Short task description.",
                },
                "prompt": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Prompt to send to the agent run.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["sync", "async"],
                    "description": "Run inline or launch in the background.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Optional sync wait timeout.",
                },
            },
            "required": ["agent_name", "description", "prompt"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
        permission=TASK_ASK,
    )


def _resolve_parent_metadata(
    parent_metadata: TaskToolParentMetadata | None,
) -> dict[str, Any]:
    if parent_metadata is None:
        return {}
    value = parent_metadata() if callable(parent_metadata) else parent_metadata
    return dict(value or {})
