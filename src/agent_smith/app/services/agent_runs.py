"""Agent run use cases."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeAlias

from agent_smith.app.auth import AppAssertionError
from agent_smith.app.context import ContextResolutionError, ContextResolver
from agent_smith.app.invocation import AgentInvocation, VerifiedActor
from agent_smith.app.ports.files import FileAuditEvent, FileAuditStore
from agent_smith.app.services.agent_run_traces import create_agent_run_trace, install_trace_hooks
from agent_smith.app.services.authentication import PrincipalAuthenticationService
from agent_smith.app.services.resources import ResourceService
from agent_smith.app.services.sessions import SessionService
from agent_smith.app.services.attachments import AttachmentService, ResolvedAttachments
from agent_smith.core.agent import AgentHarnessError
from agent_smith.core.agent.harness.context_types import RecentConversationProvider
from agent_smith.core.agent.harness.session.session import Session
from agent_smith.core.llm import get_models, get_providers
from agent_smith.core.llm.env_keys import is_provider_configured
from agent_smith.core.llm.types import AssistantMessage, JsonObject, Model, TextContent
from agent_smith.core.resources import ResourceResolver
from agent_smith.core.runtime import AgentFactory
from agent_smith.core.tools.registry import create_base_tool_registry

AgentRunEventSink: TypeAlias = Callable[[str, Any], Awaitable[None] | None]
SMITH_STREAM_VERSION = "2026-07-07"


@dataclass
class PreparedAgentInvocation:
    invocation: AgentInvocation
    actor: VerifiedActor
    principal_id: str
    stable_context: JsonObject
    turn_context: JsonObject
    session_provenance: JsonObject
    session: Session
    model: Model
    attachments: ResolvedAttachments


@dataclass
class PreparedPrompt:
    prompt: str
    agent_name: str
    context_metadata: dict[str, Any] | None
    session: Session
    principal_id: str
    model: Model
    attachments: ResolvedAttachments


class AgentRunService:
    def __init__(
        self,
        *,
        session_service: SessionService,
        resource_service: ResourceService,
        default_permission_mode: str,
        default_model_key: str,
        authentication_service: PrincipalAuthenticationService | None = None,
        context_resolver: ContextResolver | None = None,
        recent_conversation_provider: RecentConversationProvider | None = None,
        attachment_service: AttachmentService | None = None,
        file_audit_store: FileAuditStore | None = None,
    ) -> None:
        self._session_service = session_service
        self._resource_service = resource_service
        self._authentication_service = authentication_service
        self._context_resolver = context_resolver or ContextResolver()
        self._recent_conversation_provider = recent_conversation_provider
        self._attachment_service = attachment_service
        self._file_audit_store = file_audit_store
        self.default_permission_mode = default_permission_mode
        self.default_model_key = default_model_key

    def model_choices(self) -> list[dict[str, Any]]:
        choices: list[dict[str, Any]] = []
        for model in self._available_models():
            choices.append(
                {
                    "key": self._model_key(model),
                    "label": model.name,
                    "reasoning": model.reasoning,
                    "input": model.input,
                    "contextWindow": model.context_window,
                    "maxTokens": model.max_tokens,
                }
            )
        return choices

    def default_model_selection(self) -> str:
        choices = self.model_choices()
        if any(choice["key"] == self.default_model_key for choice in choices):
            return self.default_model_key
        return choices[0]["key"] if choices else ""

    def _available_models(self) -> list[Model]:
        return [
            model
            for provider in get_providers()
            if is_provider_configured(provider)
            for model in get_models(provider)
            if model.key
        ]

    @staticmethod
    def _model_key(model: Model) -> str:
        if not model.key:
            raise ValueError(f"Model {model.provider}/{model.id} has no public key")
        return model.key

    async def prepare_prompt(self, payload: dict[str, Any]) -> PreparedPrompt:
        raw_prompt = payload.get("prompt")
        if not isinstance(raw_prompt, str):
            raise ValueError("prompt must be a string")
        prompt = raw_prompt.strip()
        selected_model = self._selected_model(
            str(payload.get("modelKey")) if payload.get("modelKey") is not None else None
        )
        principal = await self._session_service.ensure_principal()
        attachments = await self._resolve_attachments(
            principal_id=principal.id,
            raw_attachments=payload.get("attachments", []),
            model=selected_model,
        )
        if not prompt and not attachments.records:
            raise ValueError("prompt or attachments is required")
        agent_name = str(
            payload.get("agentName") or self._resource_service.default_agent_name
        ).strip()
        session_id = payload.get("sessionId")
        if session_id is not None:
            session_id = str(session_id)
        raw_context_metadata = payload.get("contextMetadata")
        context_metadata = raw_context_metadata if isinstance(raw_context_metadata, dict) else None
        session = await self._session_service.open_or_create_session(session_id)
        return PreparedPrompt(
            prompt=prompt,
            agent_name=agent_name,
            context_metadata=context_metadata,
            session=session,
            principal_id=principal.id,
            model=selected_model,
            attachments=attachments,
        )

    async def run_prompt_stream(self, payload: dict[str, Any], emit: AgentRunEventSink) -> None:
        prepared = await self.prepare_prompt(payload)
        await self.run_prepared_prompt_stream(prepared, emit)

    async def run_prepared_prompt_stream(
        self, prepared: PreparedPrompt, emit: AgentRunEventSink
    ) -> None:
        try:
            store = self._resource_service.store()
            resolver = ResourceResolver([store])
            tool_registry = create_base_tool_registry(
                resources_store=store,
                resources_resolver=resolver,
                sleep_max_seconds=5,
            )
            factory = AgentFactory(
                resource_resolver=resolver,
                tool_registry=tool_registry,
                default_model=prepared.model,
                model_resolver=lambda _definition: prepared.model,
                convert_to_llm=self._attachment_converter(
                    principal_id=prepared.principal_id,
                    model=prepared.model,
                    attachments=prepared.attachments,
                ),
                default_permission_mode=self.default_permission_mode,
                context_metadata=prepared.context_metadata,
                recent_conversation_provider=self._recent_conversation_provider,
            )
            session = prepared.session
            metadata = await session.get_metadata()
            await _emit(emit, "session", metadata)

            harness = await factory.create_harness(prepared.agent_name, session=session)
            trace = create_agent_run_trace(
                flow="prompt_stream",
                run_id=str(uuid.uuid4()),
                session_id=metadata.id,
            )
            install_trace_hooks(harness, trace)

            async def emit_harness(event: Any) -> None:
                await _emit(emit, "harness", event)

            unsubscribe = harness.subscribe(emit_harness)
            try:
                response = await harness.prompt(
                    prepared.prompt,
                    {"attachments": prepared.attachments.references},
                )
            finally:
                unsubscribe()

            await _emit(
                emit,
                "done",
                {
                    "message": response,
                    "text": assistant_text(response),
                    "session": await session.get_metadata(),
                    "entries": await session.get_entries(),
                },
            )
            await trace.write_session_entries(session)
        except AgentHarnessError as exc:
            await _emit(
                emit,
                "error",
                {
                    "code": exc.code,
                    "message": "Prompt failed. Check the server log for details.",
                },
            )
        except Exception as exc:  # pragma: no cover - surfaced through transport smoke tests/logs
            await _emit(
                emit,
                "error",
                {
                    "code": exc.__class__.__name__,
                    "message": "Prompt failed. Check the server log for details.",
                },
            )

    async def prepare_invocation(
        self,
        *,
        provider_api_key: str | None = None,
        authorization: str | None,
        body: dict[str, Any],
    ) -> PreparedAgentInvocation:
        if self._authentication_service is None:
            raise AppAssertionError(
                "assertion_auth_not_configured",
                "App assertion authentication is not configured.",
            )
        invocation = AgentInvocation.model_validate(body)
        authenticated = await self._authentication_service.authenticate(
            provider_api_key=provider_api_key,
            authorization=authorization,
        )
        actor = authenticated.actor
        principal_id = authenticated.principal_id
        stable_context, turn_context, provenance = self._context_resolver.resolve(
            invocation=invocation,
            actor=actor,
            principal_id=principal_id,
        )
        selected_model = self._selected_model(invocation.payload.model_key)
        attachments = await self._resolve_attachments(
            principal_id=principal_id,
            raw_attachments=[
                item.model_dump(mode="json", by_alias=True)
                for item in invocation.payload.attachments
            ],
            model=selected_model,
        )
        if not invocation.payload.prompt.strip() and not attachments.records:
            raise ValueError("prompt or attachments is required")
        session = await self._session_service.open_or_create_session_for_principal(
            principal_id=principal_id,
            session_id=invocation.session.smith_session_id,
            provenance=provenance,
        )
        if attachments.records and self._file_audit_store is not None:
            await self._file_audit_store.append(
                [
                    FileAuditEvent(
                        principal_id=principal_id,
                        identity_provider_id=actor.provider_id,
                        actor_subject=actor.subject,
                        file_id=record.id,
                        action="file.attached",
                        outcome="succeeded",
                        correlation_id=invocation.correlation_id,
                        details={
                            "mimeType": record.detected_mime_type or record.mime_type,
                            "declaredSize": record.size_bytes,
                            "resultingStatus": record.status,
                        },
                    )
                    for record in attachments.records
                ]
            )
        return PreparedAgentInvocation(
            invocation=invocation,
            actor=actor,
            principal_id=principal_id,
            stable_context=stable_context,
            turn_context=turn_context,
            session_provenance=provenance,
            session=session,
            model=selected_model,
            attachments=attachments,
        )

    async def run_prepared_invocation_stream(
        self,
        prepared: PreparedAgentInvocation,
        emit: AgentRunEventSink,
    ) -> None:
        run_id = str(uuid.uuid4())
        sequence = 0

        async def emit_smith(event: str, data: JsonObject | dict[str, Any]) -> None:
            nonlocal sequence
            sequence += 1
            metadata = await prepared.session.get_metadata()
            await _emit(
                emit,
                event,
                {
                    "version": SMITH_STREAM_VERSION,
                    "event": event,
                    "runId": run_id,
                    "sessionId": metadata.id,
                    "sequence": sequence,
                    "createdAt": datetime.now(UTC).isoformat(),
                    "data": data,
                },
            )

        try:
            payload = prepared.invocation.payload
            prompt = payload.prompt.strip()
            agent_name = (payload.agent_name or self._resource_service.default_agent_name).strip()
            selected_model = prepared.model

            await emit_smith(
                "run.started",
                {
                    "principalId": prepared.principal_id,
                    "issuer": prepared.actor.issuer,
                    "identityProviderId": prepared.actor.provider_id,
                    "identityProviderSlug": prepared.actor.provider_slug,
                    "actorSubject": prepared.actor.subject,
                },
            )
            await emit_smith(
                "session.resolved",
                (await prepared.session.get_metadata()).model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
            )

            store = self._resource_service.store()
            resolver = ResourceResolver([store])
            tool_registry = create_base_tool_registry(
                resources_store=store,
                resources_resolver=resolver,
                sleep_max_seconds=5,
            )
            factory = AgentFactory(
                resource_resolver=resolver,
                tool_registry=tool_registry,
                default_model=selected_model,
                model_resolver=lambda _definition: selected_model,
                convert_to_llm=self._attachment_converter(
                    principal_id=prepared.principal_id,
                    model=selected_model,
                    attachments=prepared.attachments,
                ),
                default_permission_mode=self.default_permission_mode,
                context_metadata=prepared.stable_context,
                recent_conversation_provider=self._recent_conversation_provider,
            )
            harness = await factory.create_harness(agent_name, session=prepared.session)
            trace = create_agent_run_trace(
                flow="agent_invoke_stream",
                run_id=run_id,
                session_id=(await prepared.session.get_metadata()).id,
                stable_context=prepared.stable_context,
                turn_context=prepared.turn_context,
                invocation=prepared.invocation.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                ),
                actor=prepared.actor,
            )
            install_trace_hooks(harness, trace)

            async def emit_harness(event: Any) -> None:
                mapped = _map_harness_event(event)
                if mapped is not None:
                    await emit_smith(mapped[0], mapped[1])

            unsubscribe = harness.subscribe(emit_harness)
            try:
                response = await harness.prompt(
                    prompt,
                    {
                        "turnContextMetadata": prepared.turn_context,
                        "attachments": prepared.attachments.references,
                    },
                )
            finally:
                unsubscribe()

            text = assistant_text(response)
            usage = response.usage.model_dump(mode="json", by_alias=True, exclude_none=True)
            await emit_smith("usage.updated", usage)
            await trace.write_session_entries(prepared.session)
            await emit_smith(
                "run.completed",
                {
                    "message": response.model_dump(mode="json", by_alias=True, exclude_none=True),
                    "finalText": text,
                    "usage": usage,
                    "session": (await prepared.session.get_metadata()).model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                },
            )
        except Exception as exc:
            await emit_smith(
                "run.failed",
                {
                    "code": exc.__class__.__name__,
                    "message": _public_error_message(exc),
                    "retryable": isinstance(exc, (ContextResolutionError,)),
                },
            )

    def _selected_model(self, model_key: str | None):
        key = (model_key or self.default_model_selection()).strip()
        available = {self._model_key(model): model for model in self._available_models()}
        model = available.get(key)
        if model is None:
            raise ValueError(f"Unknown or unavailable model selection: {key}")
        return model

    async def _resolve_attachments(
        self,
        *,
        principal_id: str,
        raw_attachments: Any,
        model: Model,
    ) -> ResolvedAttachments:
        if self._attachment_service is None:
            if raw_attachments:
                raise ValueError("Image attachments are not configured")
            return ResolvedAttachments()
        return await self._attachment_service.resolve_current(
            principal_id=principal_id,
            raw_attachments=raw_attachments,
            model=model,
        )

    def _attachment_converter(
        self,
        *,
        principal_id: str,
        model: Model,
        attachments: ResolvedAttachments,
    ):
        if self._attachment_service is None:
            return None
        return self._attachment_service.converter(
            principal_id=principal_id,
            model=model,
            current=attachments,
        )


def assistant_text(message: AssistantMessage) -> str:
    return "\n".join(
        block.text for block in message.content if isinstance(block, TextContent)
    ).strip()


async def _emit(emit: AgentRunEventSink, event: str, data: Any) -> None:
    result = emit(event, data)
    if inspect.isawaitable(result):
        await result


def _map_harness_event(event: Any) -> tuple[str, dict[str, Any]] | None:
    event_type = getattr(event, "type", None)
    if event_type == "message_update":
        assistant_event = getattr(event, "assistant_message_event", None)
        if getattr(assistant_event, "type", None) == "text_delta":
            return ("message.delta", {"text": assistant_event.delta})
        return None
    if event_type == "message_end":
        message = getattr(event, "message", None)
        if isinstance(message, AssistantMessage):
            return (
                "message.completed",
                {
                    "message": message.model_dump(mode="json", by_alias=True, exclude_none=True),
                    "text": assistant_text(message),
                },
            )
    if event_type in {"tool_execution_start", "tool_call"}:
        return (
            "tool.started",
            {
                "toolCallId": getattr(event, "tool_call_id", None),
                "name": getattr(event, "tool_name", None),
            },
        )
    if event_type in {"tool_execution_end", "tool_result"}:
        is_error = bool(getattr(event, "is_error", False))
        name = getattr(event, "tool_name", None)
        tool_call_id = getattr(event, "tool_call_id", None)
        if event_type == "tool_result":
            content = getattr(event, "content", None)
            details = getattr(event, "details", None)
        else:
            result = getattr(event, "result", None)
            content = getattr(result, "content", None) if result is not None else None
            details = getattr(result, "details", None) if result is not None else None
        return (
            "tool.failed" if is_error else "tool.completed",
            {
                "toolCallId": tool_call_id,
                "name": name,
                "content": _jsonable_model(content),
                "details": _jsonable_model(details),
            },
        )
    return None


def _jsonable_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_jsonable_model(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable_model(item) for key, item in value.items()}
    return value


def _public_error_message(exc: Exception) -> str:
    if isinstance(exc, AgentHarnessError):
        return "Prompt failed. Check the server log for details."
    if isinstance(exc, ContextResolutionError):
        return str(exc)
    return "Prompt failed. Check the server log for details."
