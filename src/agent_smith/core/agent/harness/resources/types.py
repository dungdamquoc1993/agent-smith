"""Resolved resource snapshots consumed by one agent harness."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
