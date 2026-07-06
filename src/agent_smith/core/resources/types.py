"""Resource catalog types.

Resources are persisted/configured definitions. They are intentionally separate
from harness runtime snapshots, sessions, and concrete callable tool objects.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from agent_smith.core.llm.types import JsonObject, ModelThinkingLevel

ResourceKind = Literal[
    "skill",
    "prompt_template",
    "agent_definition",
    "mcp_server_config",
    "user_memory",
]
ResourceScope = Literal["builtin", "file", "project", "user", "session"]
ResourceSourceType = Literal["builtin", "filesystem", "memory", "plugin", "postgres"]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def resource_content_hash(content: JsonObject) -> str:
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ResourceVersion(BaseModel):
    id: str
    resource_id: str = Field(alias="resourceId")
    version: int
    content: JsonObject
    content_hash: str = Field(alias="contentHash")
    created_at: str = Field(default_factory=utc_now_iso, alias="createdAt")

    model_config = {"populate_by_name": True}


class ResourceRecord(BaseModel):
    id: str
    kind: ResourceKind
    name: str
    scope: ResourceScope = "user"
    source_type: ResourceSourceType = Field(default="memory", alias="sourceType")
    description: str | None = None
    source_uri: str | None = Field(default=None, alias="sourceUri")
    current_version: ResourceVersion = Field(alias="currentVersion")
    versions: list[ResourceVersion] = Field(default_factory=list)
    disabled: bool = False
    deleted_at: str | None = Field(default=None, alias="deletedAt")
    created_at: str = Field(default_factory=utc_now_iso, alias="createdAt")
    updated_at: str = Field(default_factory=utc_now_iso, alias="updatedAt")

    model_config = {"populate_by_name": True}

    @property
    def content(self) -> JsonObject:
        return self.current_version.content

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class ResourceCreate(BaseModel):
    kind: ResourceKind
    name: str
    content: JsonObject
    scope: ResourceScope = "user"
    source_type: ResourceSourceType = Field(default="memory", alias="sourceType")
    description: str | None = None
    source_uri: str | None = Field(default=None, alias="sourceUri")
    disabled: bool = False

    model_config = {"populate_by_name": True}


class ResourceUpdate(BaseModel):
    content: JsonObject | None = None
    description: str | None = None
    source_uri: str | None = Field(default=None, alias="sourceUri")
    disabled: bool | None = None

    model_config = {"populate_by_name": True}


class AgentModelRef(BaseModel):
    provider: str | None = None
    model_id: str = Field(alias="modelId")

    model_config = {"populate_by_name": True}


class AgentDefinition(BaseModel):
    """Persistable blueprint for spawning a harness-backed agent."""

    name: str
    description: str
    system_prompt: str = Field(alias="systemPrompt")
    when_to_use: str | None = Field(default=None, alias="whenToUse")
    tools_allow: list[str] | None = Field(default=None, alias="toolsAllow")
    tools_deny: list[str] | None = Field(default=None, alias="toolsDeny")
    skills: list[str] = Field(default_factory=list)
    prompt_templates: list[str] = Field(default_factory=list, alias="promptTemplates")
    mcp_servers: list[str] = Field(default_factory=list, alias="mcpServers")
    model: AgentModelRef | str | None = None
    thinking_level: ModelThinkingLevel = Field(default="off", alias="thinkingLevel")
    max_turns: int | None = Field(default=None, alias="maxTurns")
    permission_mode: str | None = Field(default=None, alias="permissionMode")

    model_config = {"populate_by_name": True, "extra": "allow"}


class McpServerConfig(BaseModel):
    name: str
    description: str | None = None
    config: JsonObject = Field(default_factory=dict)

    model_config = {"populate_by_name": True, "extra": "allow"}
