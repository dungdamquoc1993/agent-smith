"""Sleep tool factory."""

from __future__ import annotations

import asyncio
import time

from agent.types import AgentTool
from tools.shared.common import is_aborted, text_result
from tools.sleep.constants import SLEEP_TOOL_NAME


def create_sleep_tool(max_seconds: float = 300) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, on_update
        seconds = float(args["seconds"])
        if seconds <= 0:
            raise ValueError("seconds must be greater than 0")
        if seconds > max_seconds:
            raise ValueError(f"seconds must be less than or equal to {max_seconds:g}")

        start = time.monotonic()
        while True:
            if is_aborted(signal):
                raise RuntimeError("Operation aborted")
            elapsed = time.monotonic() - start
            remaining = seconds - elapsed
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 0.05))

        elapsed = time.monotonic() - start
        return text_result(
            f"Slept for {elapsed:.2f}s.",
            details={"seconds": seconds, "elapsedSeconds": elapsed},
        )

    return AgentTool(
        name=SLEEP_TOOL_NAME,
        label="Sleep",
        description="Wait for a specified number of seconds.",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": max_seconds,
                    "description": "Number of seconds to wait.",
                }
            },
            "required": ["seconds"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="parallel",
    )
