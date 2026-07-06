"""Transport-neutral command envelopes."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class CommandEnvelope(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="commandId")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    principal_id: str | None = Field(default=None, alias="principalId")
    session_id: str | None = Field(default=None, alias="sessionId")
    task_id: str | None = Field(default=None, alias="taskId")
    trace_id: str | None = Field(default=None, alias="traceId")
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

