"""Session tree contracts for the stateful harness."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, NotRequired, Protocol, TypedDict

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AgentMessage
from agent_smith.core.llm.types import HookPayload, JsonValue

SessionEntryType = Literal[
    "message",
    "model_change",
    "thinking_level_change",
    "active_tools_change",
    "compaction",
    "branch_summary",
    "custom",
    "custom_message",
    "label",
    "session_info",
    "leaf",
]
SessionKind = Literal["chat", "agent_run"]


class SessionMetadata(BaseModel):
    id: str
    principal_id: str | None = Field(default=None, alias="principalId")
    title: str | None = None
    kind: SessionKind = "chat"
    parent_session_id: str | None = Field(default=None, alias="parentSessionId")
    agent_name: str | None = Field(default=None, alias="agentName")
    origin_task_id: str | None = Field(default=None, alias="originTaskId")
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SessionModelRef(BaseModel):
    provider: str
    model_id: str = Field(alias="modelId")

    model_config = {"populate_by_name": True}


class SessionContext(BaseModel):
    messages: list[AgentMessage] = Field(default_factory=list)
    thinking_level: str = Field(default="off", alias="thinkingLevel")
    model: SessionModelRef | None = None
    active_tool_names: list[str] | None = Field(default=None, alias="activeToolNames")

    model_config = {"populate_by_name": True}


class SessionTreeEntry(BaseModel):
    id: str
    type: SessionEntryType
    parent_id: str | None = Field(default=None, alias="parentId")
    timestamp: str

    message: AgentMessage | None = None
    provider: str | None = None
    model_id: str | None = Field(default=None, alias="modelId")
    thinking_level: str | None = Field(default=None, alias="thinkingLevel")
    active_tool_names: list[str] | None = Field(default=None, alias="activeToolNames")
    summary: str | None = None
    first_kept_entry_id: str | None = Field(default=None, alias="firstKeptEntryId")
    tokens_before: int | None = Field(default=None, alias="tokensBefore")
    details: HookPayload | None = None
    from_hook: bool | None = Field(default=None, alias="fromHook")
    custom_type: str | None = Field(default=None, alias="customType")
    data: JsonValue | None = None
    content: HookPayload | None = None
    display: bool | None = None
    target_id: str | None = Field(default=None, alias="targetId")
    label: str | None = None
    name: str | None = None

    model_config = {"populate_by_name": True}


class PendingMessageWrite(TypedDict):
    type: Literal["message"]
    message: AgentMessage


class PendingModelChangeWrite(TypedDict):
    type: Literal["model_change"]
    provider: str
    model_id: str


class PendingThinkingLevelChangeWrite(TypedDict):
    type: Literal["thinking_level_change"]
    thinking_level: str


class PendingActiveToolsChangeWrite(TypedDict):
    type: Literal["active_tools_change"]
    active_tool_names: list[str]


class PendingSessionInfoWrite(TypedDict):
    type: Literal["session_info"]
    name: NotRequired[str]


PendingSessionWrite = (
    PendingMessageWrite
    | PendingModelChangeWrite
    | PendingThinkingLevelChangeWrite
    | PendingActiveToolsChangeWrite
    | PendingSessionInfoWrite
)


class SessionStorage(Protocol):
    async def get_metadata(self) -> SessionMetadata: ...

    async def create_entry_id(self) -> str: ...

    async def append_entry(self, entry: SessionTreeEntry) -> None: ...

    async def get_entry(self, entry_id: str) -> SessionTreeEntry | None: ...

    async def find_entries(self, entry_type: SessionEntryType) -> list[SessionTreeEntry]: ...

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]: ...

    async def get_entries(self) -> list[SessionTreeEntry]: ...

    async def get_leaf_id(self) -> str | None: ...

    async def set_leaf_id(self, entry_id: str | None) -> None: ...


def build_session_context(path_entries: Sequence[SessionTreeEntry]) -> SessionContext:
    from agent_smith.core.agent.harness.compaction import build_projected_messages

    thinking_level = "off"
    model: SessionModelRef | None = None
    active_tool_names: list[str] | None = None
    messages = build_projected_messages(list(path_entries))

    for entry in path_entries:
        if entry.type == "thinking_level_change" and entry.thinking_level is not None:
            thinking_level = entry.thinking_level
        elif entry.type == "model_change" and entry.provider and entry.model_id:
            model = SessionModelRef(provider=entry.provider, model_id=entry.model_id)
        elif entry.type == "active_tools_change":
            active_tool_names = list(entry.active_tool_names or [])
        elif entry.type == "message" and entry.message is not None and entry.message.role == "assistant":
            model = SessionModelRef(
                provider=entry.message.provider,
                model_id=entry.message.model,
            )

    return SessionContext(
        messages=messages,
        thinking_level=thinking_level,
        model=model,
        active_tool_names=active_tool_names,
    )
