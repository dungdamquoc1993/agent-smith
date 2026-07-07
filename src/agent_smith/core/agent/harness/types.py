"""Stateful agent harness types."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel, Field

from agent_smith.core.llm.types import (
    CacheRetention,
    HookPayload,
    ImageContent,
    JsonObject,
    JsonValue,
    MaybeAwaitable,
    Model,
    ModelThinkingLevel,
    ProviderPayload,
    TextContent,
)
from agent_smith.core.agent.types import AgentEvent, AgentMessage, AgentTool, StreamFn
from agent_smith.core.agent.harness.compaction import CompactionPreparation, CompactionSettings
from agent_smith.core.agent.harness.session.types import SessionContext, SessionMetadata, SessionTreeEntry
from agent_smith.core.permissions.types import PermissionModeInput


class Result(BaseModel):
    ok: bool
    value: HookPayload | None = None
    error: HookPayload | None = None


class AgentHarnessError(Exception):
    def __init__(self, code: str, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause


class Skill(BaseModel):
    name: str
    description: str
    content: str
    file_path: str = Field(alias="filePath")
    disable_model_invocation: bool | None = Field(
        default=None,
        alias="disableModelInvocation",
    )

    model_config = {"populate_by_name": True}


class PromptTemplate(BaseModel):
    name: str
    description: str | None = None
    content: str


class AgentCatalogEntry(BaseModel):
    name: str
    description: str
    when_to_use: str | None = Field(default=None, alias="whenToUse")
    tools_allow: list[str] | None = Field(default=None, alias="toolsAllow")
    tools_deny: list[str] | None = Field(default=None, alias="toolsDeny")

    model_config = {"populate_by_name": True}


class UserMemorySnapshot(BaseModel):
    content: str
    source: str = "resource:user_memory/default"
    resource_id: str | None = Field(default=None, alias="resourceId")
    resource_version_id: str | None = Field(default=None, alias="resourceVersionId")
    version: int | None = None
    content_hash: str | None = Field(default=None, alias="contentHash")

    model_config = {"populate_by_name": True}


class AgentHarnessResources(BaseModel):
    skills: list[Skill] | None = None
    prompt_templates: list[PromptTemplate] | None = Field(
        default=None,
        alias="promptTemplates",
    )
    agent_catalog: list[AgentCatalogEntry] | None = Field(
        default=None,
        alias="agentCatalog",
    )
    user_memory: UserMemorySnapshot | None = Field(default=None, alias="userMemory")

    model_config = {"populate_by_name": True}


class AgentHarnessStreamOptions(BaseModel):
    transport: str | None = None
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    max_retries: int | None = Field(default=None, alias="maxRetries")
    max_retry_delay_ms: int | None = Field(default=None, alias="maxRetryDelayMs")
    headers: dict[str, str] | None = None
    metadata: JsonObject | None = None
    cache_retention: CacheRetention | None = Field(default=None, alias="cacheRetention")

    model_config = {"populate_by_name": True, "extra": "allow"}


class AgentHarnessStreamOptionsPatch(AgentHarnessStreamOptions):
    headers: dict[str, str | None] | None = None
    metadata: dict[str, JsonValue | None] | None = None


class AgentHarnessAuth(BaseModel):
    api_key: str | None = Field(default=None, alias="apiKey")
    headers: dict[str, str] | None = None

    model_config = {"populate_by_name": True}


class AgentHarnessPromptOptions(BaseModel):
    images: list[ImageContent] | None = None


@runtime_checkable
class AgentHarnessSession(Protocol):
    async def get_metadata(self) -> SessionMetadata: ...

    async def build_context(self) -> SessionContext: ...

    async def get_branch(self, from_id: str | None = None) -> list[SessionTreeEntry]: ...

    async def get_entry(self, entry_id: str) -> SessionTreeEntry | None: ...

    async def get_entries(self) -> list[SessionTreeEntry]: ...

    async def append_message(self, message: AgentMessage) -> str: ...

    async def append_model_change(self, provider: str, model_id: str) -> str: ...

    async def append_thinking_level_change(self, thinking_level: str) -> str: ...

    async def append_active_tools_change(self, active_tool_names: list[str]) -> str: ...

    async def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: HookPayload | None = None,
        from_hook: bool | None = None,
    ) -> str: ...

    async def append_custom_entry(self, custom_type: str, data: JsonValue | None = None) -> str: ...

    async def append_session_name(self, name: str) -> str: ...


@runtime_checkable
class SystemPromptFn(Protocol):
    def __call__(
        self,
        *,
        session: AgentHarnessSession,
        model: Model,
        thinking_level: ModelThinkingLevel,
        active_tools: list[AgentTool],
        resources: "AgentHarnessResources",
    ) -> MaybeAwaitable[str]: ...


AgentHarnessAuthInput = AgentHarnessAuth | dict[str, HookPayload]
GetAgentHarnessAuthFn = Callable[[Model], MaybeAwaitable[AgentHarnessAuthInput | None]]


class AbortResult(BaseModel):
    cleared_steer: list[AgentMessage] = Field(alias="clearedSteer")
    cleared_follow_up: list[AgentMessage] = Field(alias="clearedFollowUp")

    model_config = {"populate_by_name": True}


class QueueUpdateEvent(BaseModel):
    type: Literal["queue_update"] = "queue_update"
    steer: list[AgentMessage]
    follow_up: list[AgentMessage] = Field(alias="followUp")
    next_turn: list[AgentMessage] = Field(alias="nextTurn")

    model_config = {"populate_by_name": True}


class SavePointEvent(BaseModel):
    type: Literal["save_point"] = "save_point"
    had_pending_mutations: bool = Field(alias="hadPendingMutations")

    model_config = {"populate_by_name": True}


class AbortEvent(BaseModel):
    type: Literal["abort"] = "abort"
    cleared_steer: list[AgentMessage] = Field(alias="clearedSteer")
    cleared_follow_up: list[AgentMessage] = Field(alias="clearedFollowUp")

    model_config = {"populate_by_name": True}


class SettledEvent(BaseModel):
    type: Literal["settled"] = "settled"
    next_turn_count: int = Field(alias="nextTurnCount")

    model_config = {"populate_by_name": True}


class BeforeAgentStartEvent(BaseModel):
    type: Literal["before_agent_start"] = "before_agent_start"
    prompt: str
    images: list[ImageContent] | None = None
    system_prompt: str = Field(alias="systemPrompt")
    resources: AgentHarnessResources

    model_config = {"populate_by_name": True}


class ContextEvent(BaseModel):
    type: Literal["context"] = "context"
    messages: list[AgentMessage]


class BeforeProviderRequestEvent(BaseModel):
    type: Literal["before_provider_request"] = "before_provider_request"
    model: Model
    session_id: str = Field(alias="sessionId")
    stream_options: AgentHarnessStreamOptions = Field(alias="streamOptions")

    model_config = {"populate_by_name": True}


class BeforeProviderPayloadEvent(BaseModel):
    type: Literal["before_provider_payload"] = "before_provider_payload"
    model: Model
    payload: ProviderPayload


class AfterProviderResponseEvent(BaseModel):
    type: Literal["after_provider_response"] = "after_provider_response"
    status: int
    headers: dict[str, str]


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    input: JsonObject

    model_config = {"populate_by_name": True}


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    input: JsonObject
    content: list[TextContent | ImageContent]
    details: HookPayload | None = None
    is_error: bool = Field(alias="isError")

    model_config = {"populate_by_name": True}


class ModelUpdateEvent(BaseModel):
    type: Literal["model_update"] = "model_update"
    model: Model
    previous_model: Model | None = Field(alias="previousModel")
    source: Literal["set", "restore"] = "set"

    model_config = {"populate_by_name": True}


class ThinkingLevelUpdateEvent(BaseModel):
    type: Literal["thinking_level_update"] = "thinking_level_update"
    level: ModelThinkingLevel
    previous_level: ModelThinkingLevel = Field(alias="previousLevel")

    model_config = {"populate_by_name": True}


class ToolsUpdateEvent(BaseModel):
    type: Literal["tools_update"] = "tools_update"
    tool_names: list[str] = Field(alias="toolNames")
    previous_tool_names: list[str] = Field(alias="previousToolNames")
    active_tool_names: list[str] = Field(alias="activeToolNames")
    previous_active_tool_names: list[str] = Field(alias="previousActiveToolNames")
    source: Literal["set", "restore"] = "set"

    model_config = {"populate_by_name": True}


class ResourcesUpdateEvent(BaseModel):
    type: Literal["resources_update"] = "resources_update"
    resources: AgentHarnessResources
    previous_resources: AgentHarnessResources = Field(alias="previousResources")

    model_config = {"populate_by_name": True}


class SessionBeforeCompactEvent(BaseModel):
    type: Literal["session_before_compact"] = "session_before_compact"
    preparation: CompactionPreparation
    trigger: Literal["manual", "auto"]
    custom_instructions: str | None = Field(default=None, alias="customInstructions")

    model_config = {"populate_by_name": True}


class SessionCompactEvent(BaseModel):
    type: Literal["session_compact"] = "session_compact"
    compaction_entry: SessionTreeEntry | None = Field(alias="compactionEntry")
    trigger: Literal["manual", "auto"]
    from_hook: bool = Field(alias="fromHook")

    model_config = {"populate_by_name": True}


AgentHarnessOwnEvent = (
    QueueUpdateEvent
    | SavePointEvent
    | AbortEvent
    | SettledEvent
    | BeforeAgentStartEvent
    | ContextEvent
    | BeforeProviderRequestEvent
    | BeforeProviderPayloadEvent
    | AfterProviderResponseEvent
    | ToolCallEvent
    | ToolResultEvent
    | ModelUpdateEvent
    | ThinkingLevelUpdateEvent
    | ToolsUpdateEvent
    | ResourcesUpdateEvent
    | SessionBeforeCompactEvent
    | SessionCompactEvent
)
AgentHarnessEvent = AgentEvent | AgentHarnessOwnEvent


class BeforeAgentStartResult(BaseModel):
    messages: list[AgentMessage] | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")

    model_config = {"populate_by_name": True}


class ContextResult(BaseModel):
    messages: list[AgentMessage]


class BeforeProviderRequestResult(BaseModel):
    stream_options: AgentHarnessStreamOptionsPatch | None = Field(
        default=None,
        alias="streamOptions",
    )

    model_config = {"populate_by_name": True}


class BeforeProviderPayloadResult(BaseModel):
    payload: ProviderPayload


class ToolCallResult(BaseModel):
    block: bool | None = None
    reason: str | None = None
    updated_args: JsonObject | None = Field(default=None, alias="updatedArgs")

    model_config = {"populate_by_name": True}


class ToolResultPatch(BaseModel):
    content: list[TextContent | ImageContent] | None = None
    details: HookPayload | None = None
    is_error: bool | None = Field(default=None, alias="isError")
    terminate: bool | None = None

    model_config = {"populate_by_name": True}


class SessionBeforeCompactResult(BaseModel):
    cancel: bool | None = None
    summary: str | None = None
    details: JsonObject | None = None


class TurnState(TypedDict):
    messages: list[AgentMessage]
    resources: AgentHarnessResources
    stream_options: AgentHarnessStreamOptions
    session_id: str
    system_prompt: str
    model: Model
    thinking_level: ModelThinkingLevel
    tools: list[AgentTool]
    active_tools: list[AgentTool]
    user_memory_snapshot: UserMemorySnapshot | None
    runtime_metadata_snapshot: JsonObject | None
    recent_conversations: list[Any]


class AgentHarnessOptions(BaseModel):
    session: AgentHarnessSession
    model: Model
    system_prompt: str | SystemPromptFn | None = Field(
        default=None,
        alias="systemPrompt",
        exclude=True,
    )
    resources: AgentHarnessResources | None = None
    stream_options: AgentHarnessStreamOptions | None = Field(
        default=None,
        alias="streamOptions",
    )
    get_api_key_and_headers: GetAgentHarnessAuthFn | None = (
        Field(default=None, alias="getApiKeyAndHeaders", exclude=True)
    )
    tools: list[AgentTool] | None = None
    active_tool_names: list[str] | None = Field(default=None, alias="activeToolNames")
    thinking_level: ModelThinkingLevel = Field(default="off", alias="thinkingLevel")
    stream_fn: StreamFn | None = Field(default=None, alias="streamFn", exclude=True)
    compaction_settings: CompactionSettings | None = Field(
        default=None,
        alias="compactionSettings",
    )
    permission_mode: PermissionModeInput = Field(default="default", alias="permissionMode")
    permission_resolver: Any | None = Field(
        default=None,
        alias="permissionResolver",
        exclude=True,
    )
    can_use_tool: Any | None = Field(default=None, alias="canUseTool", exclude=True)
    permission_rule_store: Any | None = Field(
        default=None,
        alias="permissionRuleStore",
        exclude=True,
    )
    context_metadata: JsonObject | None = Field(default=None, alias="contextMetadata")
    recent_conversation_provider: Any | None = Field(
        default=None,
        alias="recentConversationProvider",
        exclude=True,
    )
    is_background: bool = Field(default=False, alias="isBackground")

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }


HarnessHandler = Callable[[AgentHarnessEvent], MaybeAwaitable[HookPayload]]
