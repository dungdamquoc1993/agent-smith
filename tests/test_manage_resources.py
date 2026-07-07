from __future__ import annotations

import pytest

from agent_smith.core.agent.validation import validate_tool_arguments
from agent_smith.core.llm.types import ToolCall
from agent_smith.core.resources import (
    AgentDefinition,
    MemoryResourceStore,
    ResourceNotFoundError,
    ResourceReadOnlyError,
    ResourceResolver,
)
from helpers.resource_stores import ReadOnlyResourceStore
from agent_smith.core.tools import (
    MANAGE_RESOURCES_TOOL_NAME,
    create_base_tool_registry,
    create_manage_resources_tool,
)


def _agent_resource(
    name: str,
    system_prompt: str,
    description: str | None = None,
) -> dict:
    return {
        "kind": "agent_definition",
        "name": name,
        "description": description or f"{name} agent",
        "content": {
            "name": name,
            "description": description or f"{name} agent",
            "systemPrompt": system_prompt,
            "whenToUse": f"Use {name}",
            "toolsAllow": ["read_file"],
            "skills": ["debug"],
        },
    }


def _skill_resource(name: str, content: str, description: str | None = None) -> dict:
    return {
        "kind": "skill",
        "name": name,
        "description": description or f"{name} skill",
        "content": {
            "name": name,
            "description": description or f"{name} skill",
            "content": content,
            "filePath": f"/skills/{name}/SKILL.md",
        },
    }


@pytest.mark.asyncio
async def test_manage_resources_lists_and_reads_agent_definitions() -> None:
    base = MemoryResourceStore([_agent_resource("reviewer", "Use base review.")])
    override = MemoryResourceStore([_agent_resource("reviewer", "Use override review.")])
    tool = create_manage_resources_tool(override, resolver=ResourceResolver([base, override]))

    listed = await tool.execute(
        "res-1",
        {"kind": "agent_definition", "action": "list"},
        None,
        None,
    )
    loaded = await tool.execute(
        "res-2",
        {"kind": "agent_definition", "action": "read", "name": "reviewer"},
        None,
        None,
    )

    assert listed.details["resources"] == [
        {
            "name": "reviewer",
            "description": "reviewer agent",
            "whenToUse": "Use reviewer",
            "resource": listed.details["resources"][0]["resource"],
        }
    ]
    assert "content" not in listed.details["resources"][0]
    assert loaded.details["resource"]["content"]["systemPrompt"] == "Use override review."
    assert loaded.details["resource"]["content"]["toolsAllow"] == ["read_file"]
    assert loaded.details["resource"]["resource"]["version"] == 1


@pytest.mark.asyncio
async def test_manage_resources_agent_create_update_delete() -> None:
    store = MemoryResourceStore()
    tool = create_manage_resources_tool(store)

    created = await tool.execute(
        "res-1",
        {
            "kind": "agent_definition",
            "action": "create",
            "name": "reviewer",
            "description": "Review changes",
            "content": {
                "systemPrompt": "Review carefully.",
                "whenToUse": "Use for code review",
                "toolsAllow": ["read_file", "web_search"],
                "skills": ["debug"],
                "model": {"provider": "openai", "modelId": "gpt-test"},
                "maxTurns": 4,
            },
        },
        None,
        None,
    )
    updated = await tool.execute(
        "res-2",
        {
            "kind": "agent_definition",
            "action": "update",
            "name": "reviewer",
            "content": {
                "systemPrompt": "Review carefully and mention tests.",
                "skills": [],
                "permissionMode": "read_only",
            },
        },
        None,
        None,
    )
    await tool.execute(
        "res-3",
        {"kind": "agent_definition", "action": "delete", "name": "reviewer"},
        None,
        None,
    )

    deleted = await store.get_resource("agent_definition", "reviewer", include_deleted=True)
    created_definition = AgentDefinition.model_validate(created.details["resource"]["content"])
    updated_definition = AgentDefinition.model_validate(updated.details["resource"]["content"])

    assert created.details["resource"]["resource"]["version"] == 1
    assert created_definition.system_prompt == "Review carefully."
    assert created_definition.tools_allow == ["read_file", "web_search"]
    assert created_definition.model is not None
    assert updated.details["resource"]["resource"]["version"] == 2
    assert updated_definition.system_prompt == "Review carefully and mention tests."
    assert updated_definition.skills == []
    assert updated_definition.permission_mode == "read_only"
    assert await store.get_resource("agent_definition", "reviewer") is None
    assert deleted is not None
    assert deleted.deleted_at is not None


@pytest.mark.asyncio
async def test_manage_resources_skill_crud() -> None:
    store = MemoryResourceStore()
    tool = create_manage_resources_tool(store)

    created = await tool.execute(
        "res-1",
        {
            "kind": "skill",
            "action": "create",
            "name": "review",
            "description": "Review changes",
            "content": {
                "content": "Inspect the diff.",
                "filePath": "/skills/review/SKILL.md",
            },
        },
        None,
        None,
    )
    updated = await tool.execute(
        "res-2",
        {
            "kind": "skill",
            "action": "update",
            "name": "review",
            "content": {
                "content": "Inspect the diff and tests.",
                "disableModelInvocation": True,
            },
        },
        None,
        None,
    )
    await tool.execute(
        "res-3",
        {"kind": "skill", "action": "delete", "name": "review"},
        None,
        None,
    )

    assert created.details["resource"]["resource"]["version"] == 1
    assert updated.details["resource"]["content"] == "Inspect the diff and tests."
    assert updated.details["resource"]["disableModelInvocation"] is True
    assert await store.get_resource("skill", "review") is None


@pytest.mark.asyncio
async def test_manage_resources_prompt_template_and_mcp_config() -> None:
    store = MemoryResourceStore()
    tool = create_manage_resources_tool(store)

    template = await tool.execute(
        "res-1",
        {
            "kind": "prompt_template",
            "action": "create",
            "name": "fix",
            "content": {"content": "Fix $1"},
        },
        None,
        None,
    )
    mcp = await tool.execute(
        "res-2",
        {
            "kind": "mcp_server_config",
            "action": "create",
            "name": "github",
            "content": {"config": {"command": "github-mcp"}},
        },
        None,
        None,
    )

    assert template.details["resource"]["content"] == "Fix $1"
    assert mcp.details["resource"]["content"]["config"] == {"command": "github-mcp"}


@pytest.mark.asyncio
async def test_manage_resources_user_memory_crud() -> None:
    store = MemoryResourceStore()
    tool = create_manage_resources_tool(store)

    created = await tool.execute(
        "res-1",
        {
            "kind": "user_memory",
            "action": "create",
            "name": "default",
            "description": "Default user memory",
            "content": {"content": "User prefers concise replies."},
        },
        None,
        None,
    )
    updated = await tool.execute(
        "res-2",
        {
            "kind": "user_memory",
            "action": "update",
            "name": "default",
            "content": {"content": "User prefers short direct answers."},
        },
        None,
        None,
    )
    listed = await tool.execute(
        "res-3",
        {"kind": "user_memory", "action": "list"},
        None,
        None,
    )
    loaded = await tool.execute(
        "res-4",
        {"kind": "user_memory", "action": "read", "name": "default"},
        None,
        None,
    )
    await tool.execute(
        "res-5",
        {"kind": "user_memory", "action": "delete", "name": "default"},
        None,
        None,
    )

    assert created.details["resource"]["content"] == "User prefers concise replies."
    assert updated.details["resource"]["resource"]["version"] == 2
    assert updated.details["resource"]["content"] == "User prefers short direct answers."
    assert listed.details["resources"][0]["name"] == "default"
    assert "content" not in listed.details["resources"][0]
    assert loaded.details["resource"]["content"] == "User prefers short direct answers."
    assert await store.get_resource("user_memory", "default") is None


@pytest.mark.asyncio
async def test_manage_resources_validates_action_payloads() -> None:
    tool = create_manage_resources_tool(MemoryResourceStore())

    with pytest.raises(ValueError, match="name is required"):
        await tool.execute(
            "res-1",
            {"kind": "agent_definition", "action": "read"},
            None,
            None,
        )
    with pytest.raises(ValueError, match="content is required"):
        await tool.execute(
            "res-2",
            {"kind": "agent_definition", "action": "create", "name": "reviewer"},
            None,
            None,
        )
    with pytest.raises(ValueError, match="at least one"):
        await tool.execute(
            "res-3",
            {"kind": "agent_definition", "action": "update", "name": "reviewer"},
            None,
            None,
        )
    with pytest.raises(ResourceNotFoundError, match="Unknown agent definition"):
        await tool.execute(
            "res-4",
            {
                "kind": "agent_definition",
                "action": "update",
                "name": "missing",
                "description": "Missing",
            },
            None,
            None,
        )
    with pytest.raises(ValueError, match="non-empty string"):
        await tool.execute(
            "res-5",
            {
                "kind": "user_memory",
                "action": "create",
                "name": "default",
                "content": {"content": "   "},
            },
            None,
            None,
        )


@pytest.mark.asyncio
async def test_manage_resources_read_only_store_blocks_mutations() -> None:
    store = ReadOnlyResourceStore(
        MemoryResourceStore([_agent_resource("reviewer", "Review carefully.", "Review")])
    )
    tool = create_manage_resources_tool(store)

    listed = await tool.execute(
        "res-1",
        {"kind": "agent_definition", "action": "list"},
        None,
        None,
    )
    assert listed.details["resources"][0]["name"] == "reviewer"

    with pytest.raises(ResourceReadOnlyError):
        await tool.execute(
            "res-2",
            {
                "kind": "agent_definition",
                "action": "create",
                "name": "new",
                "content": {"systemPrompt": "Act carefully."},
            },
            None,
            None,
        )


@pytest.mark.asyncio
async def test_manage_resources_skill_list_uses_resolver_priority() -> None:
    base = MemoryResourceStore([_skill_resource("debug", "Use base logs.")])
    override = MemoryResourceStore([_skill_resource("debug", "Use override traces.")])
    tool = create_manage_resources_tool(override, resolver=ResourceResolver([base, override]))

    listed = await tool.execute("res-1", {"kind": "skill", "action": "list"}, None, None)
    loaded = await tool.execute(
        "res-2",
        {"kind": "skill", "action": "read", "name": "debug"},
        None,
        None,
    )

    assert listed.details["resources"][0]["name"] == "debug"
    assert loaded.details["resource"]["content"] == "Use override traces."


def test_manage_resources_schema_and_optional_registry() -> None:
    store = MemoryResourceStore()
    tool = create_manage_resources_tool(store)

    validate_tool_arguments(
        tool,
        ToolCall(
            id="res-1",
            name="manage_resources",
            arguments={
                "kind": "agent_definition",
                "action": "create",
                "name": "reviewer",
                "content": {"systemPrompt": "Review carefully."},
            },
        ),
    )

    base = create_base_tool_registry()
    with_resources = create_base_tool_registry(
        resources_store=store,
        resources_resolver=ResourceResolver([store]),
    )

    assert "user_memory" in tool.parameters["properties"]["kind"]["enum"]
    assert MANAGE_RESOURCES_TOOL_NAME not in base.names()
    assert MANAGE_RESOURCES_TOOL_NAME in with_resources.names()
    assert "skill" in with_resources.names()
