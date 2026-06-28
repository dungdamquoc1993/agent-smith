"""Task output retrieval tool factory."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.types import AgentTool
from tasks import TaskRuntime, TaskTimeoutError
from tools.shared.common import text_result
from tools.shared.task_serialization import task_output_to_details, task_record_to_details
from tools.task_output.constants import TASK_OUTPUT_TOOL_NAME


class TaskOutputToolInput(BaseModel):
    task_id: str = Field(min_length=1)
    block: bool = False
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_bytes: int | None = Field(default=None, ge=0)


def create_task_output_tool(
    task_runtime: TaskRuntime,
    *,
    default_timeout_seconds: float = 30,
    default_max_bytes: int = 100_000,
) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = TaskOutputToolInput.model_validate(args)
        timeout_seconds = (
            payload.timeout_seconds
            if payload.timeout_seconds is not None
            else default_timeout_seconds
        )
        max_bytes = payload.max_bytes if payload.max_bytes is not None else default_max_bytes

        retrieval_status = "success"
        if payload.block:
            try:
                record = await task_runtime.wait(
                    payload.task_id,
                    timeout_seconds=timeout_seconds,
                )
            except TaskTimeoutError:
                record = await task_runtime.get(payload.task_id)
                retrieval_status = "timeout"
        else:
            record = await task_runtime.get(payload.task_id)
            if record.status == "running":
                retrieval_status = "not_ready"

        output = await task_runtime.read_output(payload.task_id, max_bytes=max_bytes)
        details = {
            "retrievalStatus": retrieval_status,
            "task": task_record_to_details(record),
            "output": task_output_to_details(output),
        }
        if retrieval_status == "not_ready":
            message = f"Task {payload.task_id} is still running."
        elif retrieval_status == "timeout":
            message = f"Timed out waiting for task {payload.task_id}."
        else:
            message = f"Task {payload.task_id} is {record.status}."
        return text_result(message, details=details)

    return AgentTool(
        name=TASK_OUTPUT_TOOL_NAME,
        label="Task Output",
        description="Read output and status for a runtime task.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "minLength": 1},
                "block": {
                    "type": "boolean",
                    "description": "Wait for the task to finish before returning.",
                },
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Optional wait timeout when block is true.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Maximum output bytes to return from the tail.",
                },
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="parallel",
    )
