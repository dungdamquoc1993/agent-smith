"""Interface-only personal context search tool."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentTool
from agent_smith.core.permissions.tool_specs import READ_ONLY_ALLOW
from agent_smith.core.tools.personal_context.constants import PERSONAL_CONTEXT_SEARCH_TOOL_NAME
from agent_smith.core.tools.shared.common import text_result

PersonalContextSource = Literal["conversations", "user_knowledge_memory"]


class PersonalContextSearchInput(BaseModel):
    query: str = Field(
        min_length=8,
        description=(
            "Self-contained natural-language search request. Include enough names, dates, "
            "projects, or constraints for a separate memory service to understand it."
        ),
    )
    sources: list[PersonalContextSource] | None = None
    limit: int = Field(default=10, ge=1, le=40)


def create_personal_context_search_tool() -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = PersonalContextSearchInput.model_validate(args)
        return text_result(
            "personal_context.search is not implemented yet. "
            "The request was validated but no personal context backend was searched.",
            details={
                "implemented": False,
                "query": payload.query,
                "sources": payload.sources,
                "limit": payload.limit,
            },
        )

    return AgentTool(
        name=PERSONAL_CONTEXT_SEARCH_TOOL_NAME,
        label="personal_context.search",
        description=(
            "Search the user's personal context across recent conversation history and "
            "long-term user knowledge memory. The query must be self-contained and written "
            "as a natural-language request to a separate memory/search assistant."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 8,
                    "description": (
                        "Self-contained natural-language search request with enough context "
                        "for another system to understand without reading this conversation."
                    ),
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["conversations", "user_knowledge_memory"],
                    },
                    "description": "Optional context sources to search.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 40,
                    "default": 10,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
        permission=READ_ONLY_ALLOW,
    )
