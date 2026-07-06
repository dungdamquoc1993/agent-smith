"""Validate and build resource content payloads per kind."""

from __future__ import annotations

from agent_smith.core.llm.types import JsonObject
from agent_smith.core.resources import (
    AgentDefinition,
    McpServerConfig,
    agent_definition_from_record,
    mcp_server_config_from_record,
    prompt_template_from_record,
    skill_from_record,
)
from agent_smith.core.resources.types import ResourceKind, ResourceRecord
from agent_smith.core.agent.harness.types import PromptTemplate, Skill


def validate_content_for_kind(kind: ResourceKind, content: JsonObject, *, name: str) -> JsonObject:
    normalized = dict(content)
    normalized.setdefault("name", name)
    if kind == "skill":
        return _skill_to_content(Skill.model_validate(normalized))
    if kind == "prompt_template":
        return _prompt_template_to_content(PromptTemplate.model_validate(normalized))
    if kind == "agent_definition":
        return _agent_definition_to_content(AgentDefinition.model_validate(normalized))
    if kind == "mcp_server_config":
        return _mcp_server_config_to_content(McpServerConfig.model_validate(normalized))
    if kind == "user_memory":
        return _user_memory_to_content(normalized)
    raise ValueError(f"Unsupported resource kind: {kind}")


def build_create_content(
    kind: ResourceKind,
    *,
    name: str,
    content: JsonObject,
    description: str | None,
) -> tuple[JsonObject, str | None]:
    payload = dict(content)
    payload.setdefault("name", name)
    if kind == "skill":
        if "content" not in payload:
            raise ValueError("content.content is required for skill create")
        payload.setdefault("description", description or name)
        payload.setdefault("filePath", _default_skill_file_path(name))
        validated = validate_content_for_kind(kind, payload, name=name)
        return validated, str(validated.get("description") or description or name)
    if kind == "prompt_template":
        if "content" not in payload:
            raise ValueError("content.content is required for prompt_template create")
        payload.setdefault("description", description)
        validated = validate_content_for_kind(kind, payload, name=name)
        return validated, description or validated.get("description")
    if kind == "agent_definition":
        if not payload.get("systemPrompt") and not payload.get("system_prompt"):
            raise ValueError("content.systemPrompt is required for agent_definition create")
        payload.setdefault("description", description or payload.get("whenToUse") or name)
        validated = validate_content_for_kind(kind, payload, name=name)
        return validated, str(validated.get("description") or description)
    if kind == "mcp_server_config":
        if "config" not in payload:
            raise ValueError("content.config is required for mcp_server_config create")
        payload.setdefault("description", description)
        validated = validate_content_for_kind(kind, payload, name=name)
        return validated, description or validated.get("description")
    if kind == "user_memory":
        if "content" not in payload:
            raise ValueError("content.content is required for user_memory create")
        validated = validate_content_for_kind(kind, payload, name=name)
        return validated, description
    raise ValueError(f"Unsupported resource kind: {kind}")


def merge_update_content(
    kind: ResourceKind,
    *,
    name: str,
    existing: ResourceRecord,
    content_patch: JsonObject | None,
    description: str | None,
) -> tuple[JsonObject, str | None]:
    current = record_content_as_json(kind, existing)
    merged = dict(current)
    merged["name"] = name
    if content_patch:
        merged.update(content_patch)
    if description is not None:
        if kind == "skill":
            merged["description"] = description
        elif kind == "prompt_template":
            merged["description"] = description
        elif kind == "agent_definition":
            merged["description"] = description
        elif kind == "mcp_server_config":
            merged["description"] = description
        elif kind == "user_memory":
            pass
    validated = validate_content_for_kind(kind, merged, name=name)
    updated_description = description
    if kind == "agent_definition" and description is None:
        updated_description = None
    elif kind == "skill" and description is not None:
        updated_description = description
    elif kind == "prompt_template" and description is not None:
        updated_description = description
    elif kind == "mcp_server_config" and description is not None:
        updated_description = description
    elif kind == "user_memory" and description is not None:
        updated_description = description
    return validated, updated_description


def record_content_as_json(kind: ResourceKind, record: ResourceRecord) -> JsonObject:
    if kind == "skill":
        return _skill_to_content(skill_from_record(record))
    if kind == "prompt_template":
        return _prompt_template_to_content(prompt_template_from_record(record))
    if kind == "agent_definition":
        return _agent_definition_to_content(agent_definition_from_record(record))
    if kind == "mcp_server_config":
        config = mcp_server_config_from_record(record)
        return {
            "name": config.name,
            "description": config.description,
            "config": config.config,
        }
    if kind == "user_memory":
        return _user_memory_to_content(dict(record.content))
    raise ValueError(f"Unsupported resource kind: {kind}")


def _skill_to_content(skill: Skill) -> JsonObject:
    data: JsonObject = {
        "name": skill.name,
        "description": skill.description,
        "content": skill.content,
        "filePath": skill.file_path,
    }
    if skill.disable_model_invocation is not None:
        data["disableModelInvocation"] = skill.disable_model_invocation
    return data


def _prompt_template_to_content(template: PromptTemplate) -> JsonObject:
    data: JsonObject = {
        "name": template.name,
        "content": template.content,
    }
    if template.description is not None:
        data["description"] = template.description
    return data


def _agent_definition_to_content(definition: AgentDefinition) -> JsonObject:
    return definition.model_dump(mode="json", by_alias=True, exclude_none=True)


def _mcp_server_config_to_content(config: McpServerConfig) -> JsonObject:
    data: JsonObject = {
        "name": config.name,
        "config": config.config,
    }
    if config.description is not None:
        data["description"] = config.description
    return data


def _user_memory_to_content(data: JsonObject) -> JsonObject:
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content.content must be a non-empty string for user_memory")
    return {"content": content.strip()}


def _default_skill_file_path(name: str) -> str:
    return f"resource://skills/{name}/SKILL.md"
