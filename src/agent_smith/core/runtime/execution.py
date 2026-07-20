"""Shared execution lifecycle for harness-backed agents."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

from agent_smith.core.agent.harness.types import (
    AgentHarnessPromptOptions,
    AgentHarnessSession,
    LlmCallHandle,
)
from agent_smith.core.agent.types import AgentEvent
from agent_smith.core.llm.types import (
    AssistantMessage,
    Context,
    JsonValue,
    Model,
    SimpleStreamOptions,
    Usage,
    UsageCost,
)
from agent_smith.core.runtime.run_store import (
    AgentRecordingStatus,
    AgentRunFinish,
    AgentRunStart,
    AgentRunStore,
    LlmCallFinish,
    LlmCallStart,
)

logger = logging.getLogger(__name__)

AgentExecutionStage = Literal[
    "runtime",
    "run_start",
    "llm_call_start",
    "provider",
    "session_persistence",
    "run_finalize",
]
ExecutionCallback = Callable[[str], Any]
HarnessEventSink = Callable[[AgentEvent], Any]
HarnessSetup = Callable[[Any], Any]


class AgentRuntimeError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "agent_runtime_error",
        public_message: str | None = None,
        retryable: bool = False,
        stage: AgentExecutionStage = "runtime",
        run_id: str | None = None,
        usage: Usage | None = None,
        call_count: int = 0,
        recording_status: AgentRecordingStatus = "complete",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = public_message or message
        self.retryable = retryable
        self.stage = stage
        self.run_id = run_id
        self.usage = usage or Usage()
        self.call_count = call_count
        self.recording_status = recording_status
        self.cause = cause


@dataclass(frozen=True)
class AgentExecutionRequest:
    session: AgentHarnessSession
    agent_name: str
    prompt: str
    flow: str
    run_id: str | None = None
    principal_id: str | None = None
    parent_run_id: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    is_background: bool = False
    prompt_options: AgentHarnessPromptOptions | dict[str, Any] | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    on_started: ExecutionCallback | None = None
    event_sink: HarnessEventSink | None = None
    harness_setup: HarnessSetup | None = None


@dataclass(frozen=True)
class AgentExecutionResult:
    run_id: str
    message: AssistantMessage
    usage: Usage
    call_count: int
    recording_status: AgentRecordingStatus


class AgentExecutionScope:
    WRITE_TIMEOUT_SECONDS = 0.250
    FINALIZE_RETRY_DELAY_SECONDS = 0.025

    def __init__(
        self,
        *,
        store: AgentRunStore,
        run_id: str,
        session_id: str,
        principal_id: str | None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.session_id = session_id
        self.principal_id = principal_id
        self._sequence = 0
        self._usage = Usage()
        self._call_count = 0
        self._degraded = False
        self._pending_call_finishes: dict[str, LlmCallFinish] = {}

    @property
    def total_usage(self) -> Usage:
        return self._usage.model_copy(deep=True)

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def recording_status(self) -> AgentRecordingStatus:
        return "degraded" if self._degraded else "complete"

    async def start_call(
        self,
        *,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
        purpose: str,
    ) -> LlmCallHandle:
        sequence = self._sequence + 1
        handle = LlmCallHandle(
            id=str(uuid.uuid4()),
            started_at=datetime.now(UTC),
            purpose=purpose,
        )
        call = LlmCallStart(
            id=handle.id,
            run_id=self.run_id,
            session_id=self.session_id,
            principal_id=self.principal_id,
            sequence=sequence,
            purpose=purpose,
            api=model.api,
            provider=model.provider,
            requested_model=model.id,
            metadata=_safe_call_metadata(context, options),
            started_at=handle.started_at,
        )
        try:
            await asyncio.wait_for(
                self.store.start_call(call),
                timeout=self.WRITE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self._degraded = True
            raise AgentRuntimeError(
                "Unable to persist LLM call before provider request",
                code="llm_call_persistence_unavailable",
                public_message="The model request could not be started.",
                retryable=True,
                stage="llm_call_start",
                run_id=self.run_id,
                usage=self.total_usage,
                call_count=self.call_count,
                recording_status=self.recording_status,
                cause=exc,
            ) from exc
        self._sequence = sequence
        self._call_count += 1
        return handle

    async def finish_call(
        self,
        handle: LlmCallHandle,
        *,
        message: AssistantMessage | None,
        first_token_at: datetime | None,
        error: BaseException | None = None,
        aborted: bool = False,
    ) -> None:
        completed_at = datetime.now(UTC)
        usage = message.usage.model_copy(deep=True) if message is not None else Usage()
        self._usage = _sum_usage(self._usage, usage)
        stop_reason = message.stop_reason if message is not None else None
        if aborted or stop_reason == "aborted":
            status = "aborted"
        elif error is not None or stop_reason == "error":
            status = "failed"
        else:
            status = "succeeded"
        duration_ms = _elapsed_ms(handle.started_at, completed_at)
        finish = LlmCallFinish(
            call_id=handle.id,
            status=status,
            usage=usage,
            response_model=message.response_model if message is not None else None,
            provider_response_id=message.response_id if message is not None else None,
            stop_reason=stop_reason,
            first_token_at=first_token_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            time_to_first_token_ms=(
                _elapsed_ms(handle.started_at, first_token_at) if first_token_at else None
            ),
            error_type=(
                error.__class__.__name__
                if error is not None
                else "ProviderError" if status == "failed" else None
            ),
            error_code=stop_reason if status in {"failed", "aborted"} else None,
            error_message=(
                "LLM call was aborted" if status == "aborted" else "LLM call failed"
                if status == "failed"
                else None
            ),
        )
        if handle.purpose == "agent_turn" and message is not None:
            self._pending_call_finishes[handle.id] = finish
            return
        await self._persist_call_finish(finish)

    async def link_session_entry(self, call_id: str, session_entry_id: str) -> None:
        pending = self._pending_call_finishes.pop(call_id, None)
        if pending is not None:
            await self._persist_call_finish(
                replace(pending, session_entry_id=session_entry_id)
            )
            return
        if not await self._finalize(
            lambda: self.store.link_call_session_entry(call_id, session_entry_id),
            "link_session_entry",
        ):
            self._degraded = True

    async def finish_run(
        self,
        *,
        status: Literal["completed", "failed", "aborted"],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentRecordingStatus:
        if self._pending_call_finishes:
            pending = list(self._pending_call_finishes.values())
            self._pending_call_finishes.clear()
            self._degraded = True
            for finish in pending:
                await self._persist_call_finish(finish)
        finish = AgentRunFinish(
            run_id=self.run_id,
            status=status,
            recording_status=self.recording_status,
            error_code=error_code,
            error_message=error_message,
            completed_at=datetime.now(UTC),
        )
        if not await self._finalize(lambda: self.store.finish_run(finish), "finish_run"):
            self._degraded = True
        return self.recording_status

    async def _persist_call_finish(self, finish: LlmCallFinish) -> None:
        if not await self._finalize(
            lambda: self.store.finish_call(finish),
            "finish_call",
        ):
            self._degraded = True

    async def _finalize(self, operation: Callable[[], Any], label: str) -> bool:
        last_error: BaseException | None = None
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(self.FINALIZE_RETRY_DELAY_SECONDS)
            try:
                value = operation()
                if inspect.isawaitable(value):
                    await asyncio.wait_for(value, timeout=self.WRITE_TIMEOUT_SECONDS)
                return True
            except Exception as exc:
                last_error = exc
        logger.warning(
            "Agent recording finalize failed",
            extra={"run_id": self.run_id, "operation": label},
            exc_info=(
                type(last_error),
                last_error,
                last_error.__traceback__,
            )
            if last_error is not None
            else None,
        )
        return False


async def start_execution_scope(
    store: AgentRunStore,
    request: AgentExecutionRequest,
) -> AgentExecutionScope:
    metadata = await request.session.get_metadata()
    run_id = request.run_id or str(uuid.uuid4())
    principal_id = request.principal_id or metadata.principal_id
    run = AgentRunStart(
        id=run_id,
        session_id=metadata.id,
        principal_id=principal_id,
        parent_run_id=request.parent_run_id,
        agent_name=request.agent_name,
        flow=request.flow,
        correlation_id=request.correlation_id,
        trace_id=request.trace_id,
        metadata=dict(request.metadata),
        started_at=datetime.now(UTC),
    )
    try:
        await asyncio.wait_for(store.start_run(run), timeout=AgentExecutionScope.WRITE_TIMEOUT_SECONDS)
    except Exception as exc:
        raise AgentRuntimeError(
            "Unable to persist agent run before execution",
            code="run_persistence_unavailable",
            public_message="The agent run could not be started.",
            retryable=True,
            stage="run_start",
            run_id=run_id,
            recording_status="degraded",
            cause=exc,
        ) from exc
    return AgentExecutionScope(
        store=store,
        run_id=run_id,
        session_id=metadata.id,
        principal_id=principal_id,
    )


async def call_callback(callback: Callable[..., Any] | None, *args: Any) -> None:
    if callback is None:
        return
    value = callback(*args)
    if inspect.isawaitable(value):
        await value


def _sum_usage(left: Usage, right: Usage) -> Usage:
    cost = UsageCost(
        input=left.cost.input + right.cost.input,
        output=left.cost.output + right.cost.output,
        cacheRead=left.cost.cache_read + right.cost.cache_read,
        cacheWrite=left.cost.cache_write + right.cost.cache_write,
        total=left.cost.total + right.cost.total,
    )
    return Usage(
        input=left.input + right.input,
        output=left.output + right.output,
        cacheRead=left.cache_read + right.cache_read,
        cacheWrite=left.cache_write + right.cache_write,
        cacheWrite1h=(left.cache_write_1h or 0) + (right.cache_write_1h or 0),
        totalTokens=left.total_tokens + right.total_tokens,
        cost=cost,
    )


def _safe_call_metadata(context: Context, options: SimpleStreamOptions) -> dict[str, JsonValue]:
    metadata: dict[str, JsonValue] = {
        "messageCount": len(context.messages),
        "toolCount": len(context.tools or []),
    }
    if options.temperature is not None:
        metadata["temperature"] = options.temperature
    if options.max_tokens is not None:
        metadata["maxTokens"] = options.max_tokens
    if options.reasoning is not None:
        metadata["reasoning"] = options.reasoning
    if options.cache_retention is not None:
        metadata["cacheRetention"] = options.cache_retention
    return metadata


def _elapsed_ms(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() * 1_000))
