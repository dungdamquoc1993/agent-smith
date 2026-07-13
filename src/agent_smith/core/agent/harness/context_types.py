"""Context-frame data contracts with minimal dependencies."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentMessage


class RecentConversationSnapshot(BaseModel):
    id: str
    title: str | None = None
    updated_at: str | None = Field(default=None, alias="updatedAt")
    messages: list[AgentMessage] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


@runtime_checkable
class RecentConversationProvider(Protocol):
    """Read-only source of conversation snapshots used to enrich harness context."""

    async def get_recent_conversations(
        self,
        *,
        principal_id: str,
        current_session_id: str,
        limit: int = 40,
    ) -> list[RecentConversationSnapshot]: ...
