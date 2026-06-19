"""Skill resource management tool factory."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agent.harness.resources import format_skill_invocation
from agent.types import AgentTool
from ai.types import JsonObject
from resources import (
    ResourceCreate,
    ResourceNotFoundError,
    ResourceRecord,
    ResourceResolver,
    ResourceStore,
    ResourceUpdate,
    skill_from_record,
)
from tools._common import text_result

SKILLS_TOOL_NAME = "skills"
SkillsAction = Literal["list", "read", "create", "update", "delete"]


class SkillsToolInput(BaseModel):
    action: SkillsAction
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    content: str | None = None
    file_path: str | None = Field(default=None, alias="filePath")
    disable_model_invocation: bool | None = Field(
        default=None,
        alias="disableModelInvocation",
    )
    disabled: bool | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_action_payload(self) -> "SkillsToolInput":
        if self.action in {"read", "create", "update", "delete"} and not self.name:
            raise ValueError(f"name is required for {self.action}")
        if self.action == "create" and not self.content:
            raise ValueError("content is required for create")
        if self.action == "update" and not (
            self.content is not None
            or self.description is not None
            or self.file_path is not None
            or self.disable_model_invocation is not None
            or self.disabled is not None
        ):
            raise ValueError("update requires at least one editable field")
        return self


def create_skills_tool(
    store: ResourceStore,
    *,
    resolver: ResourceResolver | None = None,
) -> AgentTool:
    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        payload = SkillsToolInput.model_validate(args)

        if payload.action == "list":
            return await _list_skills(store, resolver)
        if payload.action == "read":
            return await _read_skill(store, resolver, payload.name or "")
        if payload.action == "create":
            return await _create_skill(store, payload)
        if payload.action == "update":
            return await _update_skill(store, payload)
        if payload.action == "delete":
            return await _delete_skill(store, payload.name or "")
        raise ValueError(f"Unsupported skills action: {payload.action}")

    return AgentTool(
        name=SKILLS_TOOL_NAME,
        label="Skills",
        description="List, load, create, update, or delete skill resources.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "create", "update", "delete"],
                    "description": "Skill resource action to perform.",
                },
                "name": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "content": {"type": "string"},
                "filePath": {"type": "string"},
                "disableModelInvocation": {"type": "boolean"},
                "disabled": {"type": "boolean"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
    )


async def _list_skills(
    store: ResourceStore,
    resolver: ResourceResolver | None,
):
    records = await _list_skill_records(store, resolver)
    skills = [_record_to_skill_details(record, include_content=False) for record in records]
    names = ", ".join(skill["name"] for skill in skills)
    return text_result(
        f"Found {len(skills)} skill(s){f': {names}' if names else ''}.",
        details={"action": "list", "skills": skills},
    )


async def _read_skill(
    store: ResourceStore,
    resolver: ResourceResolver | None,
    name: str,
):
    record = await _find_skill_record(store, resolver, name)
    if record is None:
        raise ResourceNotFoundError(f"Unknown skill: {name}")
    skill = skill_from_record(record)
    details = _record_to_skill_details(record, include_content=True)
    return text_result(
        format_skill_invocation(skill),
        details={"action": "read", "skill": details},
    )


async def _create_skill(store: ResourceStore, payload: SkillsToolInput):
    name = payload.name or ""
    description = payload.description or name
    file_path = payload.file_path or _default_skill_file_path(name)
    record = await store.create_resource(
        ResourceCreate(
            kind="skill",
            name=name,
            description=description,
            source_uri=payload.file_path,
            disabled=bool(payload.disabled),
            content=_skill_content(
                name=name,
                description=description,
                content=payload.content or "",
                file_path=file_path,
                disable_model_invocation=payload.disable_model_invocation,
            ),
        )
    )
    return text_result(
        f"Created skill: {name}.",
        details={
            "action": "create",
            "skill": _record_to_skill_details(record, include_content=True),
        },
    )


async def _update_skill(store: ResourceStore, payload: SkillsToolInput):
    name = payload.name or ""
    existing = await store.get_resource("skill", name)
    if existing is None:
        raise ResourceNotFoundError(f"Unknown skill: {name}")

    current = skill_from_record(existing)
    description = payload.description if payload.description is not None else current.description
    content = payload.content if payload.content is not None else current.content
    file_path = payload.file_path if payload.file_path is not None else current.file_path
    disable_model_invocation = (
        payload.disable_model_invocation
        if payload.disable_model_invocation is not None
        else current.disable_model_invocation
    )

    update = ResourceUpdate(
        content=_skill_content(
            name=name,
            description=description,
            content=content,
            file_path=file_path,
            disable_model_invocation=disable_model_invocation,
        ),
        description=description if payload.description is not None else None,
        source_uri=payload.file_path,
        disabled=payload.disabled,
    )
    record = await store.update_resource("skill", name, update)
    return text_result(
        f"Updated skill: {name}.",
        details={
            "action": "update",
            "skill": _record_to_skill_details(record, include_content=True),
        },
    )


async def _delete_skill(store: ResourceStore, name: str):
    await store.delete_resource("skill", name)
    return text_result(
        f"Deleted skill: {name}.",
        details={"action": "delete", "name": name},
    )


async def _list_skill_records(
    store: ResourceStore,
    resolver: ResourceResolver | None,
) -> list[ResourceRecord]:
    if resolver is not None:
        return await resolver.list_records("skill")
    return [
        record
        for record in await store.list_resources(kind="skill")
        if not record.disabled and not record.is_deleted
    ]


async def _find_skill_record(
    store: ResourceStore,
    resolver: ResourceResolver | None,
    name: str,
) -> ResourceRecord | None:
    for record in await _list_skill_records(store, resolver):
        if record.name == name:
            return record
    return None


def _record_to_skill_details(record: ResourceRecord, *, include_content: bool) -> JsonObject:
    skill = skill_from_record(record)
    details: JsonObject = {
        "name": skill.name,
        "description": skill.description,
        "filePath": skill.file_path,
        "disableModelInvocation": skill.disable_model_invocation,
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
        details["content"] = skill.content
    return details


def _skill_content(
    *,
    name: str,
    description: str,
    content: str,
    file_path: str,
    disable_model_invocation: bool | None,
) -> JsonObject:
    data: JsonObject = {
        "name": name,
        "description": description,
        "content": content,
        "filePath": file_path,
    }
    if disable_model_invocation is not None:
        data["disableModelInvocation"] = disable_model_invocation
    return data


def _default_skill_file_path(name: str) -> str:
    return f"resource://skills/{name}/SKILL.md"
