"""Task stopping tool factory."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentTool
from agent_smith.core.permissions.tool_specs import TASK_ASK
from agent_smith.core.tasks import TaskAlreadyFinishedError, TaskRuntime
from agent_smith.core.tools.shared.common import text_result
from agent_smith.core.tools.shared.task_serialization import task_record_to_details
from agent_smith.core.tools.task_stop.constants import TASK_STOP_TOOL_NAME


class TaskStopToolInput(BaseModel):
    task_id: str = Field(min_length=1)
    reason: str | None = None


def create_task_stop_tool(task_runtime: TaskRuntime) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = TaskStopToolInput.model_validate(args)
        stopped = True
        try:
            record = await task_runtime.stop(payload.task_id, reason=payload.reason)
        except TaskAlreadyFinishedError:
            stopped = False
            record = await task_runtime.get(payload.task_id)

        return text_result(
            f"Task {payload.task_id} is {record.status}.",
            details={
                "stopped": stopped,
                "status": record.status,
                "task": task_record_to_details(record),
            },
        )

    return AgentTool(
        name=TASK_STOP_TOOL_NAME,
        label="Task Stop",
        description="Stop a running runtime task.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "minLength": 1},
                "reason": {"type": "string"},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="parallel",
        permission=TASK_ASK,
    )
