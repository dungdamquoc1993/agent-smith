from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_smith.app.invocation import AgentInvocation, VerifiedActor
from agent_smith.app.services import agent_runs as agent_runs_module
from agent_smith.app.services.agent_runs import (
    AgentRunService,
    PreparedAgentInvocation,
    PreparedPrompt,
    SMITH_STREAM_VERSION,
)
from agent_smith.app.services.attachments import ResolvedAttachments
from agent_smith.core.llm.models import make_litellm_model
from agent_smith.core.llm.types import AssistantMessage, TextContent, Usage, UsageCost
from agent_smith.core.runtime import AgentExecutionResult, AgentRuntimeError
from helpers.sessions import MemorySessionRepo


def _message() -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="done")],
        api="litellm",
        provider="openai",
        model="gpt-test",
        usage=Usage(
            input=10,
            output=4,
            totalTokens=14,
            cost=UsageCost(input=0.01, output=0.02, total=0.03),
        ),
        timestamp=1,
    )


class _Trace:
    async def write_session_entries(self, _session) -> None:
        return None


class _FailingTrace(_Trace):
    async def write_session_entries(self, _session) -> None:
        raise OSError("trace disk unavailable")


class _SuccessfulRuntime:
    async def execute(self, request) -> AgentExecutionResult:
        assert request.run_id is not None
        if request.on_started is not None:
            await request.on_started(request.run_id)
        return AgentExecutionResult(
            run_id=request.run_id,
            message=_message(),
            usage=_message().usage,
            call_count=3,
            recording_status="degraded",
        )


class _FailedRuntime:
    async def execute(self, request) -> AgentExecutionResult:
        assert request.run_id is not None
        if request.on_started is not None:
            await request.on_started(request.run_id)
        raise AgentRuntimeError(
            "private provider detail",
            code="provider_error",
            public_message="The model provider request failed.",
            retryable=True,
            stage="provider",
            run_id=request.run_id,
            usage=Usage(input=7, output=2, totalTokens=9),
            call_count=2,
            recording_status="complete",
        )


def _service(runtime: Any) -> AgentRunService:
    service = object.__new__(AgentRunService)
    service._resource_service = SimpleNamespace(default_agent_name="assistant")
    service._create_runtime = lambda **_kwargs: runtime
    return service


async def _prepared_invocation() -> PreparedAgentInvocation:
    session = await MemorySessionRepo().create(principal_id="principal-1")
    return PreparedAgentInvocation(
        invocation=AgentInvocation.model_validate(
            {
                "payload": {"prompt": "hello", "agentName": "assistant"},
                "correlationId": "correlation-1",
                "traceId": "trace-1",
            }
        ),
        actor=VerifiedActor.model_validate(
            {
                "issuer": "parent-app",
                "subject": "user-1",
                "jti": "jti-1",
                "providerId": "provider-1",
                "providerSlug": "parent",
                "expiresAt": 9999999999,
                "actor": {"displayName": "User"},
            }
        ),
        principal_id="principal-1",
        stable_context={},
        turn_context={},
        session_provenance={},
        session=session,
        model=make_litellm_model(provider="openai", model_id="gpt-test"),
        attachments=ResolvedAttachments(),
    )


@pytest.mark.asyncio
async def test_invoke_stream_emits_aggregate_recording_contract(monkeypatch) -> None:
    monkeypatch.setattr(agent_runs_module, "create_agent_run_trace", lambda **_kwargs: _Trace())
    events: list[tuple[str, dict[str, Any]]] = []

    await _service(_SuccessfulRuntime()).run_prepared_invocation_stream(
        await _prepared_invocation(),
        lambda name, data: events.append((name, data)),
    )

    names = [name for name, _data in events]
    assert names[:2] == ["run.started", "session.resolved"]
    assert names.count("run.completed") == 1
    assert names.count("run.failed") == 0
    assert all(envelope["version"] == SMITH_STREAM_VERSION for _name, envelope in events)
    usage = next(data["data"] for name, data in events if name == "usage.updated")
    assert usage["usage"]["totalTokens"] == 14
    assert usage["callCount"] == 3
    assert usage["recording"] == {"status": "degraded"}
    completed = next(data["data"] for name, data in events if name == "run.completed")
    assert completed["usage"]["totalTokens"] == 14
    assert completed["callCount"] == 3
    assert completed["recording"] == {"status": "degraded"}


@pytest.mark.asyncio
async def test_invoke_stream_emits_one_public_safe_failed_terminal(monkeypatch) -> None:
    monkeypatch.setattr(agent_runs_module, "create_agent_run_trace", lambda **_kwargs: _Trace())
    events: list[tuple[str, dict[str, Any]]] = []

    await _service(_FailedRuntime()).run_prepared_invocation_stream(
        await _prepared_invocation(),
        lambda name, data: events.append((name, data)),
    )

    terminals = [(name, envelope["data"]) for name, envelope in events if name.startswith("run.")]
    terminals = [item for item in terminals if item[0] in {"run.completed", "run.failed"}]
    assert terminals == [
        (
            "run.failed",
            {
                "code": "provider_error",
                "message": "The model provider request failed.",
                "retryable": True,
                "stage": "provider",
                "usage": Usage(input=7, output=2, totalTokens=9).model_dump(
                    mode="json", by_alias=True
                ),
                "callCount": 2,
                "recording": {"status": "complete"},
            },
        )
    ]
    assert "private provider detail" not in str(terminals)


@pytest.mark.asyncio
async def test_optional_trace_failure_does_not_change_execution_terminal(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_runs_module,
        "create_agent_run_trace",
        lambda **_kwargs: _FailingTrace(),
    )
    events: list[tuple[str, dict[str, Any]]] = []

    await _service(_SuccessfulRuntime()).run_prepared_invocation_stream(
        await _prepared_invocation(),
        lambda name, data: events.append((name, data)),
    )

    terminal_names = [
        name for name, _data in events if name in {"run.completed", "run.failed"}
    ]
    assert terminal_names == ["run.completed"]


@pytest.mark.asyncio
async def test_legacy_prompt_done_includes_run_aggregate_and_recording(monkeypatch) -> None:
    monkeypatch.setattr(agent_runs_module, "create_agent_run_trace", lambda **_kwargs: _Trace())
    session = await MemorySessionRepo().create(principal_id="principal-1")
    prepared = PreparedPrompt(
        prompt="hello",
        agent_name="assistant",
        context_metadata=None,
        session=session,
        principal_id="principal-1",
        model=make_litellm_model(provider="openai", model_id="gpt-test"),
        attachments=ResolvedAttachments(),
    )
    events: list[tuple[str, Any]] = []

    await _service(_SuccessfulRuntime()).run_prepared_prompt_stream(
        prepared,
        lambda name, data: events.append((name, data)),
    )

    done = next(data for name, data in events if name == "done")
    assert done["runId"]
    assert done["usage"]["totalTokens"] == 14
    assert done["callCount"] == 3
    assert done["recording"] == {"status": "degraded"}
