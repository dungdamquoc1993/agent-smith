"""Persistence contracts for tracked agent executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from agent_smith.core.llm.types import JsonValue, Usage

AgentExecutionStatus = Literal["running", "completed", "failed", "aborted"]
AgentRecordingStatus = Literal["pending", "complete", "degraded"]
LlmCallStatus = Literal["started", "succeeded", "failed", "aborted"]


class AgentRunStoreError(RuntimeError):
    """A tracked execution could not be durably persisted."""


@dataclass(frozen=True)
class AgentRunStart:
    id: str
    session_id: str
    principal_id: str | None
    agent_name: str
    flow: str
    parent_run_id: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    started_at: datetime | None = None


@dataclass(frozen=True)
class AgentRunFinish:
    run_id: str
    status: Literal["completed", "failed", "aborted"]
    recording_status: Literal["complete", "degraded"]
    error_code: str | None = None
    error_message: str | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class LlmCallStart:
    id: str
    run_id: str
    session_id: str
    principal_id: str | None
    sequence: int
    purpose: str
    api: str
    provider: str
    requested_model: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    started_at: datetime | None = None


@dataclass(frozen=True)
class LlmCallFinish:
    call_id: str
    status: Literal["succeeded", "failed", "aborted"]
    usage: Usage
    session_entry_id: str | None = None
    response_model: str | None = None
    provider_response_id: str | None = None
    stop_reason: str | None = None
    first_token_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    time_to_first_token_ms: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class AgentRunStore(Protocol):
    async def start_run(self, run: AgentRunStart) -> None: ...

    async def finish_run(self, finish: AgentRunFinish) -> None: ...

    async def start_call(self, call: LlmCallStart) -> None: ...

    async def finish_call(self, finish: LlmCallFinish) -> None: ...

    async def link_call_session_entry(self, call_id: str, session_entry_id: str) -> None: ...
