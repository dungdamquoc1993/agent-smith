"""Task tool package."""

from __future__ import annotations

from typing import Any

from agent_smith.core.tools.task.constants import TASK_TOOL_NAME

__all__ = ["TASK_TOOL_NAME", "TaskToolInput", "create_task_tool"]


def __getattr__(name: str) -> Any:
    if name == "TaskToolInput":
        from agent_smith.core.tools.task.tool import TaskToolInput

        return TaskToolInput
    if name == "create_task_tool":
        from agent_smith.core.tools.task.tool import create_task_tool

        return create_task_tool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
