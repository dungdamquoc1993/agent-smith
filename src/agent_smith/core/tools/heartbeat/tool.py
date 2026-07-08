"""Interface-only recurring heartbeat scheduling tool.

Design status:
- This is a mock interface only; it validates the request and returns a stub result.
- The core scope is still open: a scheduled heartbeat may wake an agent to decide/do
  work, enqueue a system-owned job that runs without the agent, or support both.
- Do not wire this tool to real timers, persistence, or background execution until
  the scheduling ownership model is decided.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentTool
from agent_smith.core.permissions.tool_specs import TASK_ASK
from agent_smith.core.tools.heartbeat.constants import HEARTBEAT_TOOL_NAME
from agent_smith.core.tools.shared.common import text_result

ScheduledExecutionModel = Literal["wake_agent", "system_job", "either", "undecided"]


class HeartbeatToolInput(BaseModel):
    interval_seconds: int = Field(
        ge=1,
        description="Recurring interval between heartbeat executions, in seconds.",
    )
    description: str = Field(
        min_length=1,
        description="Human-readable reason for the recurring heartbeat.",
    )
    prompt: str = Field(
        min_length=1,
        description=(
            "Self-contained instruction for what should happen on each heartbeat tick. "
            "This is provisional until the execution model is decided."
        ),
    )
    execution_model: ScheduledExecutionModel = Field(
        default="undecided",
        description=(
            "Provisional ownership model: wake an agent, run a system-owned job, allow "
            "either, or leave undecided while this interface is still under design."
        ),
    )


def _design_notes() -> dict[str, object]:
    return {
        "scopeDecision": "open",
        "currentStatus": "mock_interface_only",
        "openQuestions": [
            "Should a heartbeat wake an agent so the agent can decide and execute work?",
            "Should a heartbeat register a system-owned job that runs automatically?",
            "Should both models be supported with separate lifecycle, permission, and audit semantics?",
        ],
    }


def create_heartbeat_tool() -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = HeartbeatToolInput.model_validate(args)
        return text_result(
            "heartbeat is not implemented yet. "
            "The recurring schedule request was validated but no timer, task, or agent wakeup was registered.",
            details={
                "implemented": False,
                "intervalSeconds": payload.interval_seconds,
                "description": payload.description,
                "prompt": payload.prompt,
                "executionModel": payload.execution_model,
                "design": _design_notes(),
            },
        )

    return AgentTool(
        name=HEARTBEAT_TOOL_NAME,
        label="Heartbeat",
        description=(
            "Mock interface for requesting recurring execution after a fixed interval. "
            "The implementation scope is intentionally undecided: this may eventually wake "
            "an agent, register a system-owned job, or support both."
        ),
        parameters={
            "type": "object",
            "properties": {
                "interval_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Recurring interval between heartbeat executions, in seconds.",
                },
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Human-readable reason for the recurring heartbeat.",
                },
                "prompt": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Self-contained instruction for what should happen on each heartbeat tick."
                    ),
                },
                "execution_model": {
                    "type": "string",
                    "enum": ["wake_agent", "system_job", "either", "undecided"],
                    "default": "undecided",
                    "description": (
                        "Provisional ownership model while scheduling scope is still under design."
                    ),
                },
            },
            "required": ["interval_seconds", "description", "prompt"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
        permission=TASK_ASK,
    )
