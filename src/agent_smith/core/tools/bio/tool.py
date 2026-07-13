"""Interface-only user knowledge memory update tool."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentTool
from agent_smith.core.permissions.tool_specs import MUTATING_ASK
from agent_smith.core.tools.bio.constants import BIO_TOOL_NAME
from agent_smith.core.tools.shared.common import text_result

BioAction = Literal["add", "update", "forget"]


class BioToolInput(BaseModel):
    action: BioAction
    request: str = Field(
        min_length=8,
        description=(
            "Self-contained natural-language memory change request. Include the fact, "
            "section, replacement, or forgetting target clearly enough for another system."
        ),
    )
    section_hint: str | None = Field(default=None, alias="section_hint")

    model_config = {"populate_by_name": True}


def create_bio_tool() -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = BioToolInput.model_validate(args)
        return text_result(
            "bio is not implemented yet. "
            "The request was validated but user knowledge memory was not changed.",
            details={
                "implemented": False,
                "action": payload.action,
                "request": payload.request,
                "sectionHint": payload.section_hint,
            },
        )

    return AgentTool(
        name=BIO_TOOL_NAME,
        label="bio",
        description=(
            "Request an add, update, or forget operation for the user's long-term user "
            "knowledge memory. The request must be self-contained and written as a "
            "natural-language instruction to a separate memory maintenance assistant."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "update", "forget"],
                    "description": "The requested memory operation.",
                },
                "request": {
                    "type": "string",
                    "minLength": 8,
                    "description": (
                        "Self-contained memory change request including the fact to add, "
                        "the existing memory to update, or the target to forget."
                    ),
                },
                "section_hint": {
                    "type": "string",
                    "description": "Optional target section such as Project Goal or Devices.",
                },
            },
            "required": ["action", "request"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
        permission=MUTATING_ASK,
    )
