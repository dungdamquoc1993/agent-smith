"""Interface-only personal context tool."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agent_smith.core.agent.types import AgentTool
from agent_smith.core.permissions.tool_specs import READ_ONLY_ALLOW
from agent_smith.core.tools.personal_context.constants import PERSONAL_CONTEXT_TOOL_NAME
from agent_smith.core.tools.shared.common import text_result

PersonalContextAction = Literal["search", "get"]
PersonalContextSource = Literal["conversations", "user_knowledge_memory"]


class PersonalContextToolInput(BaseModel):
    action: PersonalContextAction
    query: str | None = Field(
        default=None,
        min_length=8,
        description=(
            "Required for search. Self-contained natural-language search request. Include "
            "enough names, dates, projects, or constraints for a separate memory service."
        ),
    )
    id: str | None = Field(
        default=None,
        min_length=1,
        description="Required for get. Identifier of a personal context item to retrieve.",
    )
    sources: list[PersonalContextSource] | None = None
    limit: int = Field(default=10, ge=1, le=40)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "PersonalContextToolInput":
        if self.action == "search" and not self.query:
            raise ValueError("query is required for search")
        if self.action == "get" and not self.id:
            raise ValueError("id is required for get")
        return self


def create_personal_context_tool() -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = PersonalContextToolInput.model_validate(args)
        if payload.action == "search":
            return text_result(
                "personal_context.search is not implemented yet. "
                "The request was validated but no personal context backend was searched.",
                details={
                    "implemented": False,
                    "action": payload.action,
                    "query": payload.query,
                    "sources": payload.sources,
                    "limit": payload.limit,
                },
            )
        return text_result(
            "personal_context.get is not implemented yet. "
            "The request was validated but no personal context item was retrieved.",
            details={
                "implemented": False,
                "action": payload.action,
                "id": payload.id,
                "sources": payload.sources,
            },
        )

    return AgentTool(
        name=PERSONAL_CONTEXT_TOOL_NAME,
        label="personal_context",
        description=(
            "Search or get the user's personal context across recent conversation history "
            "and long-term user knowledge memory. Use action=search with a self-contained "
            "natural-language query, or action=get with an item id from a prior search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "get"],
                    "description": "personal_context.search or personal_context.get.",
                },
                "query": {
                    "type": "string",
                    "minLength": 8,
                    "description": (
                        "Required for search. Self-contained natural-language search request "
                        "with enough context for another system to understand without reading "
                        "this conversation."
                    ),
                },
                "id": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Required for get. Identifier of a personal context item to retrieve."
                    ),
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["conversations", "user_knowledge_memory"],
                    },
                    "description": "Optional context sources to search or constrain get.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 40,
                    "default": 10,
                    "description": "Optional result limit for search.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
        permission=READ_ONLY_ALLOW,
    )
