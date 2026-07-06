"""Application event envelopes for HTTP, SSE, and future message buses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AppEventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="eventId")
    event_type: str = Field(alias="eventType")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    principal_id: str | None = Field(default=None, alias="principalId")
    session_id: str | None = Field(default=None, alias="sessionId")
    task_id: str | None = Field(default=None, alias="taskId")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

