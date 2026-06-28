"""Skill invoke tool factory (Claude Code SkillTool-style)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent.harness.resources import (
    format_skill_invocation,
    parse_command_args,
    substitute_args,
)
from agent.harness.types import Skill
from agent.types import AgentTool
from resources import ResourceNotFoundError, ResourceResolver, skill_from_record
from tools._common import text_result
from tools.resource_management._handlers import find_resource_record

SKILL_TOOL_NAME = "skill"


class SkillToolInput(BaseModel):
    skill: str = Field(min_length=1)
    args: str | None = None


def create_skill_tool(
    *,
    resolver: ResourceResolver,
) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = SkillToolInput.model_validate(args)
        skill_name = _normalize_skill_name(payload.skill)
        record = await find_resource_record("skill", skill_name, resolver=resolver)
        if record is None:
            raise ResourceNotFoundError(f"Unknown skill: {skill_name}")

        skill = skill_from_record(record)
        if skill.disable_model_invocation:
            raise ValueError(f"Skill {skill_name} cannot be invoked by the model")

        invoked = _apply_args(skill, payload.args)
        return text_result(
            format_skill_invocation(invoked),
            details={
                "skill": skill_name,
                "args": payload.args,
            },
        )

    return AgentTool(
        name=SKILL_TOOL_NAME,
        label="Skill",
        description=(
            "Execute a skill by name with optional arguments. "
            "Available skills are listed in system-reminder messages in the conversation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "minLength": 1,
                    "description": 'The skill name. E.g., "commit", "review-pr", or "pdf".',
                },
                "args": {
                    "type": "string",
                    "description": "Optional arguments for the skill.",
                },
            },
            "required": ["skill"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
    )


def _normalize_skill_name(raw: str) -> str:
    trimmed = raw.strip()
    if trimmed.startswith("/"):
        return trimmed[1:]
    return trimmed


def _apply_args(skill: Skill, args: str | None) -> Skill:
    if not args:
        return skill
    parsed = parse_command_args(args)
    substituted = substitute_args(skill.content, parsed)
    return skill.model_copy(update={"content": substituted})
