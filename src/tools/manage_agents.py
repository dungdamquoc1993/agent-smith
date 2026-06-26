"""Agent definition resource management tool factory."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agent.types import AgentTool
from ai.types import JsonObject
from resources import (
    AgentDefinition,
    ResourceCreate,
    ResourceNotFoundError,
    ResourceRecord,
    ResourceResolver,
    ResourceStore,
    ResourceUpdate,
    agent_definition_from_record,
)
from tools._common import text_result

MANAGE_AGENTS_TOOL_NAME = "manage_agents"
ManageAgentsAction = Literal["list", "read", "create", "update", "delete"]


class ManageAgentsToolInput(BaseModel):
    action: ManageAgentsAction
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    when_to_use: str | None = Field(default=None, alias="whenToUse")
    tools_allow: list[str] | None = Field(default=None, alias="toolsAllow")
    tools_deny: list[str] | None = Field(default=None, alias="toolsDeny")
    skills: list[str] | None = None
    prompt_templates: list[str] | None = Field(default=None, alias="promptTemplates")
    mcp_servers: list[str] | None = Field(default=None, alias="mcpServers")
    model: str | JsonObject | None = None
    thinking_level: str | None = Field(default=None, alias="thinkingLevel")
    max_turns: int | None = Field(default=None, alias="maxTurns")
    permission_mode: str | None = Field(default=None, alias="permissionMode")
    disabled: bool | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ManageAgentsToolInput":
        if self.action in {"read", "create", "update", "delete"} and not self.name:
            raise ValueError(f"name is required for {self.action}")
        if self.action == "create":
            if not self.description:
                raise ValueError("description is required for create")
            if not self.system_prompt:
                raise ValueError("systemPrompt is required for create")
        if self.action == "update" and not _has_update_fields(self):
            raise ValueError("update requires at least one editable field")
        return self


def create_manage_agents_tool(
    store: ResourceStore,
    *,
    resolver: ResourceResolver | None = None,
) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = ManageAgentsToolInput.model_validate(args)

        if payload.action == "list":
            return await _list_agents(store, resolver)
        if payload.action == "read":
            return await _read_agent(store, resolver, payload.name or "")
        if payload.action == "create":
            return await _create_agent(store, payload)
        if payload.action == "update":
            return await _update_agent(store, payload)
        if payload.action == "delete":
            return await _delete_agent(store, payload.name or "")
        raise ValueError(f"Unsupported agents action: {payload.action}")

    return AgentTool(
        name=MANAGE_AGENTS_TOOL_NAME,
        label="Manage Agents",
        description="List, load, create, update, or delete agent definition resources.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "create", "update", "delete"],
                    "description": "Agent definition resource action to perform.",
                },
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "systemPrompt": {"type": "string"},
                "whenToUse": {"type": "string"},
                "toolsAllow": {"type": "array", "items": {"type": "string"}},
                "toolsDeny": {"type": "array", "items": {"type": "string"}},
                "skills": {"type": "array", "items": {"type": "string"}},
                "promptTemplates": {"type": "array", "items": {"type": "string"}},
                "mcpServers": {"type": "array", "items": {"type": "string"}},
                "model": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "provider": {"type": "string"},
                                "modelId": {"type": "string"},
                            },
                            "required": ["modelId"],
                            "additionalProperties": False,
                        },
                    ],
                },
                "thinkingLevel": {
                    "type": "string",
                    "enum": ["off", "minimal", "low", "medium", "high", "xhigh"],
                },
                "maxTurns": {"type": "integer", "minimum": 1},
                "permissionMode": {"type": "string"},
                "disabled": {"type": "boolean"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
    )


async def _list_agents(
    store: ResourceStore,
    resolver: ResourceResolver | None,
):
    records = await _list_agent_records(store, resolver)
    agents = [_record_to_agent_details(record, include_content=False) for record in records]
    names = ", ".join(agent["name"] for agent in agents)
    return text_result(
        f"Found {len(agents)} agent definition(s){f': {names}' if names else ''}.",
        details={"action": "list", "agents": agents},
    )


async def _read_agent(
    store: ResourceStore,
    resolver: ResourceResolver | None,
    name: str,
):
    record = await _find_agent_record(store, resolver, name)
    if record is None:
        raise ResourceNotFoundError(f"Unknown agent definition: {name}")
    details = _record_to_agent_details(record, include_content=True)
    return text_result(
        f"Loaded agent definition: {name}.",
        details={"action": "read", "agent": details},
    )


async def _create_agent(store: ResourceStore, payload: ManageAgentsToolInput):
    name = payload.name or ""
    definition = _definition_from_payload(payload)
    content = _definition_to_content(definition)
    record = await store.create_resource(
        ResourceCreate(
            kind="agent_definition",
            name=name,
            description=definition.description,
            disabled=bool(payload.disabled),
            content=content,
        )
    )
    return text_result(
        f"Created agent definition: {name}.",
        details={
            "action": "create",
            "agent": _record_to_agent_details(record, include_content=True),
        },
    )


async def _update_agent(store: ResourceStore, payload: ManageAgentsToolInput):
    name = payload.name or ""
    existing = await store.get_resource("agent_definition", name)
    if existing is None:
        raise ResourceNotFoundError(f"Unknown agent definition: {name}")

    current = _definition_to_content(agent_definition_from_record(existing))
    merged = _merge_definition_content(current, payload)
    definition = AgentDefinition.model_validate(merged)
    content = _definition_to_content(definition)
    record = await store.update_resource(
        "agent_definition",
        name,
        ResourceUpdate(
            content=content,
            description=definition.description if "description" in payload.model_fields_set else None,
            disabled=payload.disabled,
        ),
    )
    return text_result(
        f"Updated agent definition: {name}.",
        details={
            "action": "update",
            "agent": _record_to_agent_details(record, include_content=True),
        },
    )


async def _delete_agent(store: ResourceStore, name: str):
    await store.delete_resource("agent_definition", name)
    return text_result(
        f"Deleted agent definition: {name}.",
        details={"action": "delete", "name": name},
    )


async def _list_agent_records(
    store: ResourceStore,
    resolver: ResourceResolver | None,
) -> list[ResourceRecord]:
    if resolver is not None:
        return await resolver.list_records("agent_definition")
    return [
        record
        for record in await store.list_resources(kind="agent_definition")
        if not record.disabled and not record.is_deleted
    ]


async def _find_agent_record(
    store: ResourceStore,
    resolver: ResourceResolver | None,
    name: str,
) -> ResourceRecord | None:
    for record in await _list_agent_records(store, resolver):
        if record.name == name:
            return record
    return None


def _record_to_agent_details(record: ResourceRecord, *, include_content: bool) -> JsonObject:
    definition = agent_definition_from_record(record)
    details: JsonObject = {
        "name": definition.name,
        "description": definition.description,
        "whenToUse": definition.when_to_use,
        "resource": {
            "id": record.id,
            "scope": record.scope,
            "sourceType": record.source_type,
            "sourceUri": record.source_uri,
            "disabled": record.disabled,
            "version": record.current_version.version,
            "contentHash": record.current_version.content_hash,
            "createdAt": record.created_at,
            "updatedAt": record.updated_at,
        },
    }
    if include_content:
        details["definition"] = _definition_to_content(definition)
    return details


def _definition_from_payload(payload: ManageAgentsToolInput) -> AgentDefinition:
    content: JsonObject = {
        "name": payload.name or "",
        "description": payload.description or "",
        "systemPrompt": payload.system_prompt or "",
    }
    for field_name, alias in _AGENT_FIELD_ALIASES.items():
        if field_name in {"name", "description", "system_prompt"}:
            continue
        if field_name in payload.model_fields_set:
            content[alias] = getattr(payload, field_name)
    return AgentDefinition.model_validate(content)


def _merge_definition_content(current: JsonObject, payload: ManageAgentsToolInput) -> JsonObject:
    merged = dict(current)
    merged["name"] = payload.name or str(merged.get("name") or "")
    for field_name, alias in _AGENT_FIELD_ALIASES.items():
        if field_name in {"name", "disabled"}:
            continue
        if field_name in payload.model_fields_set:
            merged[alias] = getattr(payload, field_name)
    return merged


def _definition_to_content(definition: AgentDefinition) -> JsonObject:
    return definition.model_dump(mode="json", by_alias=True, exclude_none=True)


def _has_update_fields(payload: ManageAgentsToolInput) -> bool:
    return any(
        field in payload.model_fields_set
        for field in _EDITABLE_UPDATE_FIELDS
    )


_EDITABLE_UPDATE_FIELDS = {
    "description",
    "system_prompt",
    "when_to_use",
    "tools_allow",
    "tools_deny",
    "skills",
    "prompt_templates",
    "mcp_servers",
    "model",
    "thinking_level",
    "max_turns",
    "permission_mode",
    "disabled",
}

_AGENT_FIELD_ALIASES = {
    "name": "name",
    "description": "description",
    "system_prompt": "systemPrompt",
    "when_to_use": "whenToUse",
    "tools_allow": "toolsAllow",
    "tools_deny": "toolsDeny",
    "skills": "skills",
    "prompt_templates": "promptTemplates",
    "mcp_servers": "mcpServers",
    "model": "model",
    "thinking_level": "thinkingLevel",
    "max_turns": "maxTurns",
    "permission_mode": "permissionMode",
}
