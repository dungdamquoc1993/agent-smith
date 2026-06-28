"""Unified resource catalog management tool factory."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agent.types import AgentTool
from ai.types import JsonObject
from permission.tool_specs import MUTATING_ASK
from permission.types import PermissionDecision
from resources import ResourceKind, ResourceResolver, ResourceStore
from tools.manage_resources.constants import MANAGE_RESOURCES_TOOL_NAME, RESOURCE_KINDS
from tools.shared.resource_management._handlers import (
    create_resource,
    delete_resource,
    list_resources,
    read_resource,
    update_resource,
)

ManageResourcesAction = Literal["list", "read", "create", "update", "delete"]


class ManageResourcesToolInput(BaseModel):
    kind: ResourceKind
    action: ManageResourcesAction
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    content: JsonObject | None = None
    disabled: bool | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ManageResourcesToolInput":
        if self.action in {"read", "create", "update", "delete"} and not self.name:
            raise ValueError(f"name is required for {self.action}")
        if self.action == "create" and not self.content:
            raise ValueError("content is required for create")
        if self.action == "update" and self.content is None and self.description is None and self.disabled is None:
            raise ValueError("update requires at least one of content, description, or disabled")
        return self


def create_manage_resources_tool(
    store: ResourceStore,
    *,
    resolver: ResourceResolver | None = None,
) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = ManageResourcesToolInput.model_validate(args)

        if payload.action == "list":
            return await list_resources(store, resolver, payload.kind)
        if payload.action == "read":
            return await read_resource(store, resolver, payload.kind, payload.name or "")
        if payload.action == "create":
            return await create_resource(
                store,
                kind=payload.kind,
                name=payload.name or "",
                content=payload.content or {},
                description=payload.description,
                disabled=bool(payload.disabled),
            )
        if payload.action == "update":
            return await update_resource(
                store,
                kind=payload.kind,
                name=payload.name or "",
                content=payload.content,
                description=payload.description,
                disabled=payload.disabled,
            )
        if payload.action == "delete":
            return await delete_resource(store, payload.kind, payload.name or "")
        raise ValueError(f"Unsupported manage_resources action: {payload.action}")

    async def check_permissions(tool_input: JsonObject) -> PermissionDecision | None:
        action = str(tool_input.get("action", ""))
        if action in {"list", "read"}:
            return PermissionDecision.allow(source="tool_check:read_only_action")
        return None

    return AgentTool(
        name=MANAGE_RESOURCES_TOOL_NAME,
        label="Manage Resources",
        description=(
            "List, load, create, update, or delete catalog resources. "
            "Kinds: skill, prompt_template, agent_definition, mcp_server_config. "
            "MCP credentials are managed separately from mcp_server_config records."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(RESOURCE_KINDS),
                    "description": "Resource kind to manage.",
                },
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "create", "update", "delete"],
                    "description": "Catalog action to perform.",
                },
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "content": {
                    "type": "object",
                    "description": (
                        "Kind-specific content. skill: {content, description?, filePath?, disableModelInvocation?}. "
                        "prompt_template: {content, description?}. "
                        "agent_definition: {systemPrompt, description?, whenToUse?, toolsAllow?, toolsDeny?, skills?, promptTemplates?, mcpServers?, model?, thinkingLevel?, maxTurns?, permissionMode?}. "
                        "mcp_server_config: {config, description?}."
                    ),
                    "additionalProperties": True,
                },
                "disabled": {"type": "boolean"},
            },
            "required": ["kind", "action"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
        permission=MUTATING_ASK,
        check_permissions=check_permissions,
    )
