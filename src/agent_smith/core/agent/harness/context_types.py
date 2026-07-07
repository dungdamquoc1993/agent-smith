"""Context-frame data contracts with minimal dependencies."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentMessage


class RecentConversationSnapshot(BaseModel):
    id: str
    title: str | None = None
    updated_at: str | None = Field(default=None, alias="updatedAt")
    messages: list[AgentMessage] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
