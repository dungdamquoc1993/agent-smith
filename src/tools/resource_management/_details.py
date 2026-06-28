"""Format resource records for manage_resources tool responses."""

from __future__ import annotations

from ai.types import JsonObject
from resources import (
    ResourceRecord,
    agent_definition_from_record,
    mcp_server_config_from_record,
    prompt_template_from_record,
    skill_from_record,
)
from resources.types import ResourceKind


def resource_metadata(record: ResourceRecord) -> JsonObject:
    return {
        "id": record.id,
        "kind": record.kind,
        "scope": record.scope,
        "sourceType": record.source_type,
        "sourceUri": record.source_uri,
        "disabled": record.disabled,
        "version": record.current_version.version,
        "contentHash": record.current_version.content_hash,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
    }


def record_to_summary(record: ResourceRecord, *, include_content: bool) -> JsonObject:
    if record.kind == "skill":
        return _skill_summary(record, include_content=include_content)
    if record.kind == "prompt_template":
        return _prompt_template_summary(record, include_content=include_content)
    if record.kind == "agent_definition":
        return _agent_definition_summary(record, include_content=include_content)
    if record.kind == "mcp_server_config":
        return _mcp_server_config_summary(record, include_content=include_content)
    raise ValueError(f"Unsupported resource kind: {record.kind}")


def kind_label(kind: ResourceKind) -> str:
    return {
        "skill": "skill",
        "prompt_template": "prompt template",
        "agent_definition": "agent definition",
        "mcp_server_config": "MCP server config",
    }[kind]


def _skill_summary(record: ResourceRecord, *, include_content: bool) -> JsonObject:
    skill = skill_from_record(record)
    details: JsonObject = {
        "name": skill.name,
        "description": skill.description,
        "filePath": skill.file_path,
        "disableModelInvocation": skill.disable_model_invocation,
        "resource": resource_metadata(record),
    }
    if include_content:
        details["content"] = skill.content
    return details


def _prompt_template_summary(record: ResourceRecord, *, include_content: bool) -> JsonObject:
    template = prompt_template_from_record(record)
    details: JsonObject = {
        "name": template.name,
        "description": template.description,
        "resource": resource_metadata(record),
    }
    if include_content:
        details["content"] = template.content
    return details


def _agent_definition_summary(record: ResourceRecord, *, include_content: bool) -> JsonObject:
    definition = agent_definition_from_record(record)
    details: JsonObject = {
        "name": definition.name,
        "description": definition.description,
        "whenToUse": definition.when_to_use,
        "resource": resource_metadata(record),
    }
    if include_content:
        details["content"] = definition.model_dump(mode="json", by_alias=True, exclude_none=True)
    return details


def _mcp_server_config_summary(record: ResourceRecord, *, include_content: bool) -> JsonObject:
    config = mcp_server_config_from_record(record)
    details: JsonObject = {
        "name": config.name,
        "description": config.description,
        "resource": resource_metadata(record),
    }
    if include_content:
        details["content"] = {
            "name": config.name,
            "description": config.description,
            "config": config.config,
        }
    return details
