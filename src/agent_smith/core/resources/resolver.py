"""Resolve resource catalog records into runtime snapshots."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_smith.core.agent.harness.resources import (
    AgentHarnessResources,
    PromptTemplate,
    Skill,
    UserMemorySnapshot,
)
from agent_smith.core.llm.types import JsonObject
from agent_smith.core.resources.store import ResourceStore
from agent_smith.core.resources.types import (
    AgentDefinition,
    McpServerConfig,
    ResourceKind,
    ResourceRecord,
)


class ResolvedResources(BaseModel):
    harness_resources: AgentHarnessResources = Field(alias="harnessResources")
    agent_definitions: list[AgentDefinition] = Field(default_factory=list, alias="agentDefinitions")
    mcp_server_configs: dict[str, JsonObject] = Field(
        default_factory=dict,
        alias="mcpServerConfigs",
    )
    records: list[ResourceRecord] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ResourceResolver:
    """Resolve resources from low-to-high priority stores."""

    def __init__(self, stores: list[ResourceStore] | None = None) -> None:
        self.stores = list(stores or [])

    async def list_records(self, kind: ResourceKind | None = None) -> list[ResourceRecord]:
        resolved: dict[tuple[ResourceKind, str], ResourceRecord] = {}
        for store in self.stores:
            for record in await store.list_resources(kind=kind):
                if record.disabled or record.is_deleted:
                    continue
                resolved[(record.kind, record.name)] = record
        return sorted(resolved.values(), key=lambda record: (record.kind, record.name))

    async def resolve(self) -> ResolvedResources:
        records = await self.list_records()
        skills = [
            skill_from_record(record)
            for record in records
            if record.kind == "skill"
        ]
        prompt_templates = [
            prompt_template_from_record(record)
            for record in records
            if record.kind == "prompt_template"
        ]
        agent_definitions = [
            agent_definition_from_record(record)
            for record in records
            if record.kind == "agent_definition"
        ]
        mcp_server_configs = {
            record.name: mcp_server_config_from_record(record).config
            for record in records
            if record.kind == "mcp_server_config"
        }
        user_memory_record = next(
            (
                record
                for record in records
                if record.kind == "user_memory" and record.name == "default"
            ),
            None,
        )
        return ResolvedResources(
            harness_resources=AgentHarnessResources(
                skills=skills,
                prompt_templates=prompt_templates,
                user_memory=(
                    user_memory_snapshot_from_record(user_memory_record)
                    if user_memory_record is not None
                    else None
                ),
            ),
            agent_definitions=agent_definitions,
            mcp_server_configs=mcp_server_configs,
            records=records,
        )

    async def resolve_harness_resources(self) -> AgentHarnessResources:
        return (await self.resolve()).harness_resources

    async def get_agent_definition(self, name: str) -> AgentDefinition | None:
        for definition in (await self.resolve()).agent_definitions:
            if definition.name == name:
                return definition
        return None


def skill_from_record(record: ResourceRecord) -> Skill:
    data = dict(record.content)
    data.setdefault("name", record.name)
    data.setdefault("description", record.description or record.name)
    data.setdefault("content", "")
    data.setdefault("filePath", record.source_uri or f"{record.source_type}://{record.name}")
    return Skill.model_validate(data)


def prompt_template_from_record(record: ResourceRecord) -> PromptTemplate:
    data = dict(record.content)
    data.setdefault("name", record.name)
    data.setdefault("description", record.description)
    data.setdefault("content", "")
    return PromptTemplate.model_validate(data)


def agent_definition_from_record(record: ResourceRecord) -> AgentDefinition:
    data = dict(record.content)
    data.setdefault("name", record.name)
    data.setdefault("description", record.description or data.get("whenToUse") or record.name)
    if "systemPrompt" not in data and "system_prompt" not in data and "prompt" in data:
        data["systemPrompt"] = data["prompt"]
    if "whenToUse" not in data and "when_to_use" not in data:
        data["whenToUse"] = data.get("description")
    return AgentDefinition.model_validate(data)


def mcp_server_config_from_record(record: ResourceRecord) -> McpServerConfig:
    data = dict(record.content)
    data.setdefault("name", record.name)
    if "config" not in data:
        data = {
            "name": data["name"],
            "description": data.get("description"),
            "config": record.content,
        }
    return McpServerConfig.model_validate(data)


def user_memory_snapshot_from_record(record: ResourceRecord) -> UserMemorySnapshot:
    content = record.content.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("user_memory content.content must be a non-empty string")
    return UserMemorySnapshot(
        content=content.strip(),
        source=f"resource:{record.kind}/{record.name}",
        resource_id=record.id,
        resource_version_id=record.current_version.id,
        version=record.current_version.version,
        content_hash=record.current_version.content_hash,
    )
