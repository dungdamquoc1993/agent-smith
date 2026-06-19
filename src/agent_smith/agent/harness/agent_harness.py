"""Stateful agent harness built on top of the low-level agent loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from agent_smith.ai import complete_simple
from agent_smith.ai.events import AssistantMessageEventStream
from agent_smith.ai.types import (
    AssistantMessage,
    Context,
    HookPayload,
    ImageContent,
    Model,
    SimpleStreamOptions,
    TextContent,
    UserMessage,
)
from agent_smith.agent.agent_loop import run_agent_loop
from agent_smith.agent.agent_loop.utils import call, now_ms
from agent_smith.agent.harness.compaction import (
    CompactionPreparation,
    CompactionResult,
    CompactionSettings,
    SUMMARIZATION_SYSTEM_PROMPT,
    default_compaction_settings,
    estimate_context_tokens,
    microcompact_messages,
    prepare_compaction,
    should_compact,
    summarization_prompt,
)
from agent_smith.agent.harness.resources import (
    format_prompt_template_invocation,
    format_skill_invocation,
)
from agent_smith.agent.harness.session.types import PendingSessionWrite
from agent_smith.agent.harness.types import (
    AbortEvent,
    AbortResult,
    AgentHarnessAuth,
    AgentHarnessError,
    AgentHarnessEvent,
    AgentHarnessOptions,
    AgentHarnessPromptOptions,
    AgentHarnessResources,
    AgentHarnessSession,
    AgentHarnessStreamOptions,
    AgentHarnessStreamOptionsPatch,
    BeforeAgentStartEvent,
    BeforeAgentStartResult,
    BeforeProviderRequestEvent,
    BeforeProviderRequestResult,
    ContextEvent,
    ContextResult,
    HarnessHandler,
    ModelUpdateEvent,
    QueueUpdateEvent,
    ResourcesUpdateEvent,
    SavePointEvent,
    SettledEvent,
    SessionBeforeCompactEvent,
    SessionBeforeCompactResult,
    SessionCompactEvent,
    ThinkingLevelUpdateEvent,
    ToolCallEvent,
    ToolCallResult,
    ToolResultEvent,
    ToolResultPatch,
    ToolsUpdateEvent,
    TurnState,
)
from agent_smith.agent.types import (
    AbortSignal,
    AfterToolCallContext,
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentTool,
    BeforeToolCallContext,
    MessageEndEvent,
    StreamFn,
    TurnEndEvent,
)

SUBSCRIBER_EVENT_TYPE = "*"


def create_user_message(text: str, images: list[ImageContent] | None = None) -> UserMessage:
    if images:
        return UserMessage(content=[TextContent(text=text), *images], timestamp=now_ms())
    return UserMessage(content=text, timestamp=now_ms())


def create_failure_message(model: Model, error: Exception, aborted: bool) -> AssistantMessage:
    return AssistantMessage(
        api=model.api,
        provider=model.provider,
        model=model.id,
        content=[TextContent(text="")],
        stop_reason="aborted" if aborted else "error",
        error_message=str(error),
        timestamp=now_ms(),
    )


def _assistant_text(message: AssistantMessage) -> str:
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent)).strip()


def clone_stream_options(options: AgentHarnessStreamOptions | None) -> AgentHarnessStreamOptions:
    return AgentHarnessStreamOptions.model_validate(
        options.model_dump(mode="python", by_alias=True) if options else {}
    )


def merge_headers(*headers: dict[str, str] | None) -> dict[str, str] | None:
    merged: dict[str, str] = {}
    for value in headers:
        if value:
            merged.update(value)
    return merged or None


def apply_stream_options_patch(
    base: AgentHarnessStreamOptions,
    patch: AgentHarnessStreamOptionsPatch | dict[str, Any] | None,
) -> AgentHarnessStreamOptions:
    if patch is None:
        return clone_stream_options(base)
    resolved = (
        patch
        if isinstance(patch, AgentHarnessStreamOptionsPatch)
        else AgentHarnessStreamOptionsPatch.model_validate(patch)
    )
    data = base.model_dump(mode="python", by_alias=True, exclude_none=True)
    patch_data = resolved.model_dump(mode="python", by_alias=True, exclude_unset=True)

    for key, value in patch_data.items():
        if key in {"headers", "metadata"}:
            if value is None:
                data.pop(key, None)
                continue
            current = dict(data.get(key) or {})
            for entry_key, entry_value in value.items():
                if entry_value is None:
                    current.pop(entry_key, None)
                else:
                    current[entry_key] = entry_value
            if current:
                data[key] = current
            else:
                data.pop(key, None)
        else:
            data[key] = value

    return AgentHarnessStreamOptions.model_validate(data)


class AgentHarness:
    def __init__(self, options: AgentHarnessOptions | dict[str, Any] | None = None, **kwargs: Any) -> None:
        data: dict[str, Any] = {}
        if options is not None:
            if isinstance(options, AgentHarnessOptions):
                data.update(
                    {
                        field_name: getattr(options, field_name)
                        for field_name in AgentHarnessOptions.model_fields
                    }
                )
            else:
                data.update(options)
        data.update(kwargs)
        resolved = AgentHarnessOptions.model_validate(data)

        self.env: Any | None = data.get("env")
        self.session: AgentHarnessSession = resolved.session
        self.model: Model = resolved.model
        self.thinking_level = resolved.thinking_level
        self.system_prompt = resolved.system_prompt
        self.stream_options = clone_stream_options(resolved.stream_options)
        self.get_api_key_and_headers = resolved.get_api_key_and_headers
        self.resources = resolved.resources or AgentHarnessResources()
        self.compaction_settings = resolved.compaction_settings or default_compaction_settings()
        self.tools = {tool.name: tool for tool in resolved.tools or []}
        self.active_tool_names = (
            list(resolved.active_tool_names)
            if resolved.active_tool_names is not None
            else list(self.tools.keys())
        )
        self.stream_fn = resolved.stream_fn

        self.phase = "idle"
        self._run_signal: asyncio.Event | None = None
        self._run_task: asyncio.Task[Any] | None = None
        self._pending_session_writes: list[PendingSessionWrite] = []
        self._steer_queue: list[AgentMessage] = []
        self._follow_up_queue: list[AgentMessage] = []
        self._next_turn_queue: list[AgentMessage] = []
        self._handlers: dict[str, set[HarnessHandler]] = {}
        self._auto_compact_failures = 0
        self._is_compacting = False

        self._validate_tool_names(self.active_tool_names)

    async def prompt(
        self,
        text: str,
        options: AgentHarnessPromptOptions | dict[str, Any] | None = None,
    ) -> AssistantMessage:
        if self.phase != "idle":
            raise AgentHarnessError("busy", "AgentHarness is busy")
        self.phase = "turn"
        self._run_signal = asyncio.Event()
        try:
            turn_state = await self._create_turn_state()
            prompt_options = self._resolve_prompt_options(options)
            return await self._execute_turn(
                turn_state,
                text,
                prompt_options.images if prompt_options else None,
            )
        except AgentHarnessError:
            self.phase = "idle"
            raise
        except Exception as exc:
            self.phase = "idle"
            raise AgentHarnessError("unknown", str(exc), exc) from exc
        finally:
            self._run_signal = None

    async def skill(self, name: str, additional_instructions: str | None = None) -> AssistantMessage:
        skill = next((candidate for candidate in self.resources.skills or [] if candidate.name == name), None)
        if skill is None:
            raise AgentHarnessError("invalid_argument", f"Unknown skill: {name}")
        return await self.prompt(format_skill_invocation(skill, additional_instructions))

    async def prompt_from_template(
        self,
        name: str,
        args: list[str] | None = None,
    ) -> AssistantMessage:
        template = next(
            (candidate for candidate in self.resources.prompt_templates or [] if candidate.name == name),
            None,
        )
        if template is None:
            raise AgentHarnessError("invalid_argument", f"Unknown prompt template: {name}")
        return await self.prompt(format_prompt_template_invocation(template, args or []))

    async def compact(
        self,
        custom_instructions: str | None = None,
        settings: CompactionSettings | dict[str, Any] | None = None,
    ) -> CompactionResult | None:
        if self.phase != "idle":
            raise AgentHarnessError("busy", "AgentHarness is busy")
        resolved_settings = self._resolve_compaction_settings(settings)
        return await self._compact("manual", custom_instructions, resolved_settings)

    async def steer(
        self,
        text: str,
        options: AgentHarnessPromptOptions | dict[str, Any] | None = None,
    ) -> None:
        if self.phase == "idle":
            raise AgentHarnessError("invalid_state", "Cannot steer while idle")
        resolved_options = self._resolve_prompt_options(options)
        self._steer_queue.append(create_user_message(text, resolved_options.images if resolved_options else None))
        await self._emit_queue_update()

    async def follow_up(
        self,
        text: str,
        options: AgentHarnessPromptOptions | dict[str, Any] | None = None,
    ) -> None:
        if self.phase == "idle":
            raise AgentHarnessError("invalid_state", "Cannot follow up while idle")
        resolved_options = self._resolve_prompt_options(options)
        self._follow_up_queue.append(create_user_message(text, resolved_options.images if resolved_options else None))
        await self._emit_queue_update()

    async def next_turn(
        self,
        text: str,
        options: AgentHarnessPromptOptions | dict[str, Any] | None = None,
    ) -> None:
        resolved_options = self._resolve_prompt_options(options)
        self._next_turn_queue.append(create_user_message(text, resolved_options.images if resolved_options else None))
        await self._emit_queue_update()

    async def append_message(self, message: AgentMessage) -> None:
        if self.phase == "idle":
            await self.session.append_message(message)
        else:
            self._pending_session_writes.append({"type": "message", "message": message})

    async def set_model(self, model: Model) -> None:
        previous = self.model
        if self.phase == "idle":
            await self.session.append_model_change(model.provider, model.id)
        else:
            self._pending_session_writes.append(
                {"type": "model_change", "provider": model.provider, "model_id": model.id}
            )
        self.model = model
        await self._emit_own(ModelUpdateEvent(model=model, previous_model=previous))

    async def set_thinking_level(self, level: str) -> None:
        previous = self.thinking_level
        if self.phase == "idle":
            await self.session.append_thinking_level_change(level)
        else:
            self._pending_session_writes.append(
                {"type": "thinking_level_change", "thinking_level": level}
            )
        self.thinking_level = level
        await self._emit_own(ThinkingLevelUpdateEvent(level=level, previous_level=previous))

    async def set_tools(
        self,
        tools: list[AgentTool],
        active_tool_names: list[str] | None = None,
    ) -> None:
        previous_tool_names = list(self.tools.keys())
        previous_active_tool_names = list(self.active_tool_names)
        next_tools = {tool.name: tool for tool in tools}
        if len(next_tools) != len(tools):
            raise AgentHarnessError("invalid_argument", "Duplicate tool name(s)")
        next_active_tool_names = active_tool_names or self.active_tool_names
        self._validate_tool_names(next_active_tool_names, next_tools)
        if self.phase == "idle":
            await self.session.append_active_tools_change(next_active_tool_names)
        else:
            self._pending_session_writes.append(
                {"type": "active_tools_change", "active_tool_names": list(next_active_tool_names)}
            )
        self.tools = next_tools
        self.active_tool_names = list(next_active_tool_names)
        await self._emit_own(
            ToolsUpdateEvent(
                tool_names=list(self.tools.keys()),
                previous_tool_names=previous_tool_names,
                active_tool_names=list(self.active_tool_names),
                previous_active_tool_names=previous_active_tool_names,
            )
        )

    async def set_active_tools(self, tool_names: list[str]) -> None:
        previous_tool_names = list(self.tools.keys())
        previous_active_tool_names = list(self.active_tool_names)
        self._validate_tool_names(tool_names)
        if self.phase == "idle":
            await self.session.append_active_tools_change(tool_names)
        else:
            self._pending_session_writes.append(
                {"type": "active_tools_change", "active_tool_names": list(tool_names)}
            )
        self.active_tool_names = list(tool_names)
        await self._emit_own(
            ToolsUpdateEvent(
                tool_names=list(self.tools.keys()),
                previous_tool_names=previous_tool_names,
                active_tool_names=list(self.active_tool_names),
                previous_active_tool_names=previous_active_tool_names,
            )
        )

    async def set_resources(self, resources: AgentHarnessResources) -> None:
        previous = self.get_resources()
        self.resources = resources.model_copy(deep=True)
        await self._emit_own(ResourcesUpdateEvent(resources=self.resources, previous_resources=previous))

    def get_resources(self) -> AgentHarnessResources:
        return self.resources.model_copy(deep=True)

    async def set_stream_options(self, stream_options: AgentHarnessStreamOptions) -> None:
        self.stream_options = clone_stream_options(stream_options)

    def get_stream_options(self) -> AgentHarnessStreamOptions:
        return clone_stream_options(self.stream_options)

    async def set_compaction_settings(self, settings: CompactionSettings | dict[str, Any]) -> None:
        self.compaction_settings = self._resolve_compaction_settings(settings)
        self._auto_compact_failures = 0

    def get_compaction_settings(self) -> CompactionSettings:
        return self.compaction_settings.model_copy(deep=True)

    async def abort(self) -> AbortResult:
        cleared_steer = list(self._steer_queue)
        cleared_follow_up = list(self._follow_up_queue)
        self._steer_queue.clear()
        self._follow_up_queue.clear()
        if self._run_signal:
            self._run_signal.set()
        await self._emit_queue_update()
        await self.wait_for_idle()
        await self._emit_own(AbortEvent(cleared_steer=cleared_steer, cleared_follow_up=cleared_follow_up))
        return AbortResult(cleared_steer=cleared_steer, cleared_follow_up=cleared_follow_up)

    async def wait_for_idle(self) -> None:
        if self._run_task:
            await self._run_task

    def subscribe(self, listener: HarnessHandler) -> Callable[[], None]:
        self._handlers.setdefault(SUBSCRIBER_EVENT_TYPE, set()).add(listener)

        def unsubscribe() -> None:
            self._handlers.get(SUBSCRIBER_EVENT_TYPE, set()).discard(listener)

        return unsubscribe

    def on(
        self,
        event_type: str,
        handler: HarnessHandler,
    ) -> Callable[[], None]:
        self._handlers.setdefault(event_type, set()).add(handler)

        def unsubscribe() -> None:
            self._handlers.get(event_type, set()).discard(handler)

        return unsubscribe

    async def _execute_turn(
        self,
        turn_state: TurnState,
        text: str,
        images: list[ImageContent] | None = None,
    ) -> AssistantMessage:
        messages: list[AgentMessage] = [create_user_message(text, images)]
        if self._next_turn_queue:
            queued_messages = list(self._next_turn_queue)
            self._next_turn_queue.clear()
            await self._emit_queue_update()
            messages = [*queued_messages, messages[0]]

        before_result = await self._emit_hook(
            BeforeAgentStartEvent(
                prompt=text,
                images=images,
                system_prompt=turn_state["system_prompt"],
                resources=turn_state["resources"],
            ),
            BeforeAgentStartResult,
        )
        if before_result and before_result.messages:
            messages.extend(before_result.messages)

        turn_state = await self._maybe_auto_compact(turn_state, messages)
        active_turn_state = turn_state

        def get_turn_state() -> TurnState:
            return active_turn_state

        def set_turn_state(next_turn_state: TurnState) -> None:
            nonlocal active_turn_state
            active_turn_state = next_turn_state

        async def run() -> list[AgentMessage]:
            return await run_agent_loop(
                messages,
                self._create_context(active_turn_state, before_result.system_prompt if before_result else None),
                self._create_loop_config(get_turn_state, set_turn_state),
                self._handle_agent_event,
                self._run_signal,
                self._create_stream_fn(get_turn_state),
            )

        self._run_task = asyncio.create_task(run())
        try:
            new_messages = await self._run_task
        except Exception as exc:
            await self._emit_run_failure(active_turn_state["model"], exc)
            raise
        finally:
            self._run_task = None
            await self._flush_pending_session_writes()

        for message in reversed(new_messages):
            if message.role == "assistant":
                return message
        raise AgentHarnessError("invalid_state", "AgentHarness prompt completed without an assistant message")

    async def _create_turn_state(self) -> TurnState:
        context = await self.session.build_context()
        active_tools = [
            self.tools[name]
            for name in self.active_tool_names
            if name in self.tools
        ]
        resources = self.get_resources()
        system_prompt = "You are a helpful assistant."
        if isinstance(self.system_prompt, str):
            system_prompt = self.system_prompt
        elif self.system_prompt:
            system_prompt = await call(
                self.system_prompt(
                    session=self.session,
                    model=self.model,
                    thinking_level=self.thinking_level,
                    active_tools=active_tools,
                    resources=resources,
                )
            )
        metadata = await self.session.get_metadata()
        return {
            "messages": context.messages,
            "resources": resources,
            "stream_options": clone_stream_options(self.stream_options),
            "session_id": metadata.id,
            "system_prompt": system_prompt,
            "model": self.model,
            "thinking_level": self.thinking_level,
            "tools": list(self.tools.values()),
            "active_tools": active_tools,
        }

    def _resolve_compaction_settings(
        self,
        settings: CompactionSettings | dict[str, Any] | None,
    ) -> CompactionSettings:
        if settings is None:
            return self.compaction_settings.model_copy(deep=True)
        if isinstance(settings, CompactionSettings):
            return settings.model_copy(deep=True)
        return CompactionSettings.model_validate(settings)

    async def _maybe_auto_compact(
        self,
        turn_state: TurnState,
        extra_messages: list[AgentMessage] | None = None,
    ) -> TurnState:
        settings = self.compaction_settings
        if not settings.enabled or self._is_compacting:
            return turn_state
        if self._auto_compact_failures >= settings.max_consecutive_failures:
            return turn_state

        messages = [*turn_state["messages"], *(extra_messages or [])]
        estimated_tokens = estimate_context_tokens(
            microcompact_messages(messages, settings.microcompact)
        )
        if not should_compact(estimated_tokens, turn_state["model"].context_window, settings):
            return turn_state

        try:
            result = await self._compact("auto", None, settings)
        except Exception:
            self._auto_compact_failures += 1
            return turn_state
        if result is None:
            return turn_state

        self._auto_compact_failures = 0
        return await self._create_turn_state()

    async def _compact(
        self,
        trigger: str,
        custom_instructions: str | None,
        settings: CompactionSettings,
    ) -> CompactionResult | None:
        if self._is_compacting or not settings.enabled:
            return None

        self._is_compacting = True
        try:
            preparation = prepare_compaction(await self.session.get_branch(), settings)
            if preparation is None:
                return None

            event = SessionBeforeCompactEvent(
                preparation=preparation,
                trigger=trigger,
                custom_instructions=custom_instructions,
            )
            await self._emit_any(event)
            hook_result = await self._emit_hook(event, SessionBeforeCompactResult)
            if hook_result and hook_result.cancel:
                return None

            from_hook = bool(hook_result and hook_result.summary)
            summary = (
                hook_result.summary.strip()
                if hook_result and hook_result.summary
                else await self._generate_compaction_summary(preparation, custom_instructions)
            )
            if not summary:
                raise AgentHarnessError("compaction", "Compaction summary was empty")

            details = hook_result.details if hook_result else None
            entry_id = await self.session.append_compaction(
                summary=summary,
                first_kept_entry_id=preparation.first_kept_entry_id,
                tokens_before=preparation.tokens_before,
                details=details,
                from_hook=from_hook,
            )
            entry = await self.session.get_entry(entry_id)
            await self._emit_own(
                SessionCompactEvent(
                    compaction_entry=entry,
                    trigger=trigger,
                    from_hook=from_hook,
                )
            )
            return CompactionResult(
                summary=summary,
                first_kept_entry_id=preparation.first_kept_entry_id,
                tokens_before=preparation.tokens_before,
                details=details,
            )
        finally:
            self._is_compacting = False

    async def _generate_compaction_summary(
        self,
        preparation: CompactionPreparation,
        custom_instructions: str | None,
    ) -> str:
        prompt = summarization_prompt(preparation)
        if custom_instructions:
            prompt = (
                f"{prompt}\n\nAdditional user instructions for this compaction:\n"
                f"{custom_instructions.strip()}"
            )

        auth = await self._get_auth(self.model)
        metadata = await self.session.get_metadata()
        request_options = await self._emit_before_provider_request(
            self.model,
            metadata.id,
            self.stream_options,
            auth.headers if auth else None,
        )
        option_data = request_options.model_dump(mode="python", by_alias=True, exclude_none=True)
        option_data["sessionId"] = metadata.id
        option_data["maxTokens"] = min(
            self.model.max_tokens,
            max(1_024, preparation.settings.reserve_tokens // 4),
        )
        if auth and auth.api_key:
            option_data["apiKey"] = auth.api_key

        response = await complete_simple(
            self.model,
            Context(
                system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
                messages=[UserMessage(content=prompt, timestamp=now_ms())],
            ),
            SimpleStreamOptions.model_validate(option_data),
        )
        if response.stop_reason in ("error", "aborted"):
            raise AgentHarnessError(
                "compaction",
                response.error_message or f"Compaction failed with stop reason: {response.stop_reason}",
            )
        return _assistant_text(response)

    def _create_context(self, turn_state: TurnState, system_prompt: str | None = None) -> AgentContext:
        return AgentContext(
            system_prompt=system_prompt or turn_state["system_prompt"],
            messages=list(turn_state["messages"]),
            tools=list(turn_state["active_tools"]),
        )

    def _create_loop_config(
        self,
        get_turn_state: Callable[[], TurnState],
        set_turn_state: Callable[[TurnState], None],
    ) -> AgentLoopConfig:
        turn_state = get_turn_state()
        reasoning = None if turn_state["thinking_level"] == "off" else turn_state["thinking_level"]

        async def transform_context(
            messages: list[AgentMessage],
            _signal: AbortSignal | None,
        ) -> list[AgentMessage]:
            compacted = microcompact_messages(messages, self.compaction_settings.microcompact)
            result = await self._emit_hook(ContextEvent(messages=compacted), ContextResult)
            return result.messages if result else compacted

        async def before_tool_call(
            context: BeforeToolCallContext,
            _signal: AbortSignal | None,
        ) -> dict[str, HookPayload] | None:
            result = await self._emit_hook(
                ToolCallEvent(
                    tool_call_id=context.tool_call.id,
                    tool_name=context.tool_call.name,
                    input=context.args if isinstance(context.args, dict) else {"value": context.args},
                ),
                ToolCallResult,
            )
            return result.model_dump(exclude_none=True) if result else None

        async def after_tool_call(
            context: AfterToolCallContext,
            _signal: AbortSignal | None,
        ) -> dict[str, HookPayload] | None:
            result = await self._emit_hook(
                ToolResultEvent(
                    tool_call_id=context.tool_call.id,
                    tool_name=context.tool_call.name,
                    input=context.args if isinstance(context.args, dict) else {"value": context.args},
                    content=context.result.content,
                    details=context.result.details,
                    is_error=context.is_error,
                ),
                ToolResultPatch,
            )
            return result.model_dump(exclude_unset=True, by_alias=True) if result else None

        async def prepare_next_turn(_context: Any) -> AgentLoopTurnUpdate:
            await self._flush_pending_session_writes()
            next_turn_state = await self._create_turn_state()
            next_turn_state = await self._maybe_auto_compact(next_turn_state)
            set_turn_state(next_turn_state)
            return AgentLoopTurnUpdate(
                context=self._create_context(next_turn_state),
                model=next_turn_state["model"],
                thinking_level=next_turn_state["thinking_level"],
            )

        return AgentLoopConfig(
            model=turn_state["model"],
            reasoning=reasoning,
            transform_context=transform_context,
            before_tool_call=before_tool_call,
            after_tool_call=after_tool_call,
            prepare_next_turn=prepare_next_turn,
            get_steering_messages=self._drain_steer_queue,
            get_follow_up_messages=self._drain_follow_up_queue,
        )

    def _create_stream_fn(self, get_turn_state: Callable[[], TurnState]) -> StreamFn:
        async def stream_fn(
            model: Model,
            context: Context,
            options: SimpleStreamOptions | None = None,
        ) -> AssistantMessageEventStream:
            turn_state = get_turn_state()
            auth = await self._get_auth(model)
            request_options = await self._emit_before_provider_request(
                model,
                turn_state["session_id"],
                turn_state["stream_options"],
                auth.headers if auth else None,
            )
            option_data = request_options.model_dump(mode="python", by_alias=True, exclude_none=True)
            if options:
                option_data.update(options.model_dump(mode="python", by_alias=True, exclude_none=True))
            option_data["sessionId"] = turn_state["session_id"]
            if auth and auth.api_key:
                option_data["apiKey"] = auth.api_key
            response = (self.stream_fn or _default_stream_fn)(model, context, SimpleStreamOptions(**option_data))
            return await call(response)

        return stream_fn

    async def _emit_before_provider_request(
        self,
        model: Model,
        session_id: str,
        base_options: AgentHarnessStreamOptions,
        auth_headers: dict[str, str] | None,
    ) -> AgentHarnessStreamOptions:
        snapshot = clone_stream_options(base_options)
        snapshot.headers = merge_headers(snapshot.headers, auth_headers)
        result = await self._emit_hook(
            BeforeProviderRequestEvent(
                model=model,
                session_id=session_id,
                stream_options=snapshot,
            ),
            BeforeProviderRequestResult,
        )
        if result and result.stream_options:
            snapshot = apply_stream_options_patch(snapshot, result.stream_options)
        return snapshot

    async def _get_auth(self, model: Model) -> AgentHarnessAuth | None:
        if not self.get_api_key_and_headers:
            return None
        raw = await call(self.get_api_key_and_headers(model))
        if raw is None:
            return None
        return raw if isinstance(raw, AgentHarnessAuth) else AgentHarnessAuth.model_validate(raw)

    async def _drain_steer_queue(self) -> list[AgentMessage]:
        messages = list(self._steer_queue)
        self._steer_queue.clear()
        if messages:
            await self._emit_queue_update()
        return messages

    async def _drain_follow_up_queue(self) -> list[AgentMessage]:
        messages = list(self._follow_up_queue)
        self._follow_up_queue.clear()
        if messages:
            await self._emit_queue_update()
        return messages

    async def _handle_agent_event(self, event: AgentEvent) -> None:
        if isinstance(event, MessageEndEvent) or event.type == "message_end":
            await self.session.append_message(event.message)
            await self._emit_any(event)
            return
        if isinstance(event, TurnEndEvent) or event.type == "turn_end":
            await self._emit_any(event)
            had_pending_mutations = bool(self._pending_session_writes)
            await self._flush_pending_session_writes()
            await self._emit_own(SavePointEvent(had_pending_mutations=had_pending_mutations))
            return
        if event.type == "agent_end":
            await self._flush_pending_session_writes()
            self.phase = "idle"
            await self._emit_any(event)
            await self._emit_own(SettledEvent(next_turn_count=len(self._next_turn_queue)))
            return
        await self._emit_any(event)

    async def _emit_run_failure(self, model: Model, error: Exception) -> None:
        failure = create_failure_message(model, error, bool(self._run_signal and self._run_signal.is_set()))
        await self.session.append_message(failure)

    async def _flush_pending_session_writes(self) -> None:
        while self._pending_session_writes:
            write = self._pending_session_writes.pop(0)
            write_type = write["type"]
            if write_type == "message":
                await self.session.append_message(write["message"])
            elif write_type == "model_change":
                await self.session.append_model_change(write["provider"], write["model_id"])
            elif write_type == "thinking_level_change":
                await self.session.append_thinking_level_change(write["thinking_level"])
            elif write_type == "active_tools_change":
                await self.session.append_active_tools_change(write["active_tool_names"])
            elif write_type == "session_info":
                await self.session.append_session_name(write.get("name") or "")

    def _resolve_prompt_options(
        self,
        options: AgentHarnessPromptOptions | dict[str, Any] | None,
    ) -> AgentHarnessPromptOptions | None:
        if options is None:
            return None
        if isinstance(options, AgentHarnessPromptOptions):
            return options
        return AgentHarnessPromptOptions.model_validate(options)

    async def _emit_queue_update(self) -> None:
        await self._emit_own(
            QueueUpdateEvent(
                steer=list(self._steer_queue),
                follow_up=list(self._follow_up_queue),
                next_turn=list(self._next_turn_queue),
            )
        )

    async def _emit_own(self, event: AgentHarnessEvent) -> None:
        await self._emit_any(event)

    async def _emit_any(self, event: AgentHarnessEvent) -> None:
        for listener in list(self._handlers.get(SUBSCRIBER_EVENT_TYPE, set())):
            await call(listener(event))

    async def _emit_hook(self, event: AgentHarnessEvent, result_type: Any) -> Any | None:
        handlers = list(self._handlers.get(event.type, set()))
        if not handlers:
            return None
        result = None
        for handler in handlers:
            raw = await call(handler(event))
            if raw is not None:
                result = raw if isinstance(raw, result_type) else result_type.model_validate(raw)
        return result

    def _validate_tool_names(
        self,
        tool_names: list[str],
        tools: dict[str, AgentTool] | None = None,
    ) -> None:
        if len(set(tool_names)) != len(tool_names):
            raise AgentHarnessError("invalid_argument", "Duplicate active tool name(s)")
        available = tools or self.tools
        missing = [name for name in tool_names if name not in available]
        if missing:
            raise AgentHarnessError("invalid_argument", f"Unknown tool(s): {', '.join(missing)}")

    promptFromTemplate = prompt_from_template
    followUp = follow_up
    nextTurn = next_turn
    appendMessage = append_message
    setModel = set_model
    setThinkingLevel = set_thinking_level
    setTools = set_tools
    setActiveTools = set_active_tools
    setResources = set_resources
    getResources = get_resources
    setStreamOptions = set_stream_options
    getStreamOptions = get_stream_options
    setCompactionSettings = set_compaction_settings
    getCompactionSettings = get_compaction_settings
    waitForIdle = wait_for_idle


async def _default_stream_fn(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    from agent_smith.ai import stream_simple

    return stream_simple(model, context, options)
