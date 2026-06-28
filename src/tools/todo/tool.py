"""Stateless todo tool factory."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from agent.types import AgentTool
from permission.tool_specs import READ_ONLY_ALLOW
from tools.shared.common import text_result
from tools.todo.constants import TODO_WRITE_TOOL_NAME

TodoStatus = Literal["pending", "in_progress", "completed"]


class TodoItem(BaseModel):
    content: str = Field(min_length=1)
    status: TodoStatus
    id: str | None = None


class TodoWriteInput(BaseModel):
    todos: list[TodoItem]


def create_todo_write_tool() -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = TodoWriteInput.model_validate(args)
        todos = [todo.model_dump(mode="python", exclude_none=True) for todo in payload.todos]
        counts = Counter(todo["status"] for todo in todos)
        return text_result(
            f"Todo list updated with {len(todos)} item(s).",
            details={
                "todos": todos,
                "counts": {
                    "pending": counts["pending"],
                    "inProgress": counts["in_progress"],
                    "completed": counts["completed"],
                },
            },
        )

    return AgentTool(
        name=TODO_WRITE_TOOL_NAME,
        label="Todo Write",
        description="Replace the current stateless todo checklist with the provided list.",
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string", "minLength": 1},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["content", "status"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
        },
        execute=execute,
        permission=READ_ONLY_ALLOW,
    )
