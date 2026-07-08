"""Interface-only fixed-time cronjob scheduling tool.

Design status:
- This is a mock interface only; it validates the request and returns a stub result.
- The core scope is still open: a scheduled cronjob may wake an agent to decide/do
  work, enqueue a system-owned job that runs without the agent, or support both.
- Do not wire this tool to real schedulers, persistence, or background execution until
  the scheduling ownership model is decided.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentTool
from agent_smith.core.permissions.tool_specs import TASK_ASK
from agent_smith.core.tools.cronjob.constants import CRONJOB_TOOL_NAME
from agent_smith.core.tools.shared.common import text_result

ScheduledExecutionModel = Literal["wake_agent", "system_job", "either", "undecided"]


class CronjobToolInput(BaseModel):
    run_at: str = Field(
        min_length=1,
        description=(
            "Fixed execution time. Prefer an ISO-8601 datetime; include timezone or set "
            "timezone explicitly so the future implementation can resolve it safely."
        ),
    )
    timezone: str | None = Field(
        default=None,
        description="Optional IANA timezone, for example Asia/Ho_Chi_Minh.",
    )
    description: str = Field(
        min_length=1,
        description="Human-readable reason for the fixed-time cronjob.",
    )
    prompt: str = Field(
        min_length=1,
        description=(
            "Self-contained instruction for what should happen at the scheduled time. "
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
            "Should a cronjob wake an agent so the agent can decide and execute work?",
            "Should a cronjob register a system-owned job that runs automatically?",
            "Should both models be supported with separate lifecycle, permission, and audit semantics?",
        ],
    }


def create_cronjob_tool() -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = CronjobToolInput.model_validate(args)
        return text_result(
            "cronjob is not implemented yet. "
            "The fixed-time schedule request was validated but no scheduler entry, task, or agent wakeup was registered.",
            details={
                "implemented": False,
                "runAt": payload.run_at,
                "timezone": payload.timezone,
                "description": payload.description,
                "prompt": payload.prompt,
                "executionModel": payload.execution_model,
                "design": _design_notes(),
            },
        )

    return AgentTool(
        name=CRONJOB_TOOL_NAME,
        label="Cronjob",
        description=(
            "Mock interface for requesting execution at a fixed time. The implementation "
            "scope is intentionally undecided: this may eventually wake an agent, register "
            "a system-owned job, or support both."
        ),
        parameters={
            "type": "object",
            "properties": {
                "run_at": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Fixed execution time. Prefer an ISO-8601 datetime with timezone."
                    ),
                },
                "timezone": {
                    "type": "string",
                    "description": "Optional IANA timezone, for example Asia/Ho_Chi_Minh.",
                },
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Human-readable reason for the fixed-time cronjob.",
                },
                "prompt": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Self-contained instruction for what should happen at the scheduled time."
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
            "required": ["run_at", "description", "prompt"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
        permission=TASK_ASK,
    )
