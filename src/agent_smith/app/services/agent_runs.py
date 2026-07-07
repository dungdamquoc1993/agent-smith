"""Agent run use cases."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from agent_smith.app.services.resources import ResourceService
from agent_smith.app.services.sessions import SessionService
from agent_smith.core.agent import AgentHarnessError
from agent_smith.core.llm import get_model, make_litellm_model, register_model
from agent_smith.core.llm.types import AssistantMessage, TextContent
from agent_smith.core.resources import ResourceResolver
from agent_smith.core.runtime import AgentFactory
from agent_smith.core.tools.registry import create_base_tool_registry
from agent_smith.infra.persistence import PostgresRecentConversationProvider

AgentRunEventSink: TypeAlias = Callable[[str, Any], Awaitable[None] | None]


class AgentRunService:
    def __init__(
        self,
        *,
        session_service: SessionService,
        resource_service: ResourceService,
        default_permission_mode: str,
        openai_model_id: str,
        gemma_model_id: str,
        gemma_upstream_model: str,
        gemma_base_url: str,
        gemma_api_key: str,
        default_model_key: str,
    ) -> None:
        self._session_service = session_service
        self._resource_service = resource_service
        self.default_permission_mode = default_permission_mode
        self.openai_model_id = openai_model_id
        self.gemma_model_id = gemma_model_id
        self.gemma_upstream_model = gemma_upstream_model
        self.gemma_base_url = gemma_base_url
        self.gemma_api_key = gemma_api_key
        self.default_model_key = default_model_key

    def register_local_models(self) -> None:
        register_model(self._gemma_model())

    def model_choices(self) -> list[dict[str, str]]:
        return [
            {
                "key": "openai",
                "label": f"OpenAI · {self.openai_model_id}",
                "provider": "openai",
                "modelId": self.openai_model_id,
            },
            {
                "key": "gemma",
                "label": f"Gemma local · {self.gemma_upstream_model}",
                "provider": "local",
                "modelId": self.gemma_model_id,
                "baseUrl": self.gemma_base_url,
            },
        ]

    def default_model_selection(self) -> str:
        keys = {choice["key"] for choice in self.model_choices()}
        return self.default_model_key if self.default_model_key in keys else "openai"

    async def run_prompt_stream(self, payload: dict[str, Any], emit: AgentRunEventSink) -> None:
        try:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                raise ValueError("prompt is required")
            agent_name = str(payload.get("agentName") or self._resource_service.default_agent_name).strip()
            session_id = payload.get("sessionId")
            if session_id is not None:
                session_id = str(session_id)
            raw_context_metadata = payload.get("contextMetadata")
            context_metadata = raw_context_metadata if isinstance(raw_context_metadata, dict) else None
            selected_model = self._selected_model(
                str(payload.get("modelKey")) if payload.get("modelKey") is not None else None
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
                default_permission_mode=self.default_permission_mode,
                context_metadata=context_metadata,
                recent_conversation_provider=PostgresRecentConversationProvider(
                    self._session_service.session_factory
                ),
            )
            session = await self._session_service.open_or_create_session(session_id)
            metadata = await session.get_metadata()
            await _emit(emit, "session", metadata)

            harness = await factory.create_harness(agent_name, session=session)

            async def emit_harness(event: Any) -> None:
                await _emit(emit, "harness", event)

            unsubscribe = harness.subscribe(emit_harness)
            try:
                response = await harness.prompt(prompt)
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

    def _openai_model(self):
        return get_model("openai", self.openai_model_id) or make_litellm_model(
            provider="openai",
            model_id=self.openai_model_id,
        )

    def _gemma_model(self):
        return get_model("local", self.gemma_model_id) or make_litellm_model(
            provider="local",
            model_id=self.gemma_model_id,
            name="Gemma 4 E2B local",
            litellm_model=f"openai/{self.gemma_upstream_model}",
            base_url=self.gemma_base_url,
            reasoning=True,
            input=["text", "image"],
            context_window=128_000,
            max_tokens=4096,
            provider_options={
                "api_key": self.gemma_api_key,
                "ollama_native": True,
                "ollama_think": True,
            },
        )

    def _selected_model(self, model_key: str | None):
        key = (model_key or self.default_model_selection()).strip()
        if key == "openai":
            return self._openai_model()
        if key == "gemma":
            return self._gemma_model()
        raise ValueError(f"Unknown model selection: {key}")


def assistant_text(message: AssistantMessage) -> str:
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent)).strip()


async def _emit(emit: AgentRunEventSink, event: str, data: Any) -> None:
    result = emit(event, data)
    if inspect.isawaitable(result):
        await result
