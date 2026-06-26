from __future__ import annotations

import pytest

from agent.validation import validate_tool_arguments
from ai.types import ToolCall
from resources import (
    AgentDefinition,
    MemoryResourceStore,
    ResourceNotFoundError,
    ResourceReadOnlyError,
    ResourceResolver,
)
from helpers.resource_stores import ReadOnlyResourceStore
from tools import AGENTS_TOOL_NAME, create_agents_tool, create_base_tool_registry


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


@pytest.mark.asyncio
async def test_agents_tool_lists_and_reads_resolved_agent_definitions() -> None:
    base = MemoryResourceStore([_agent_resource("reviewer", "Use base review.")])
    override = MemoryResourceStore([_agent_resource("reviewer", "Use override review.")])
    tool = create_agents_tool(override, resolver=ResourceResolver([base, override]))

    listed = await tool.execute("agents-1", {"action": "list"}, None, None)
    loaded = await tool.execute("agents-2", {"action": "read", "name": "reviewer"}, None, None)

    assert listed.details["agents"] == [
        {
            "name": "reviewer",
            "description": "reviewer agent",
            "whenToUse": "Use reviewer",
            "resource": listed.details["agents"][0]["resource"],
        }
    ]
    assert "definition" not in listed.details["agents"][0]
    assert loaded.details["agent"]["definition"]["systemPrompt"] == "Use override review."
    assert loaded.details["agent"]["definition"]["toolsAllow"] == ["read_file"]
    assert loaded.details["agent"]["resource"]["version"] == 1


@pytest.mark.asyncio
async def test_agents_tool_create_update_delete_versions() -> None:
    store = MemoryResourceStore()
    tool = create_agents_tool(store)

    created = await tool.execute(
        "agents-1",
        {
            "action": "create",
            "name": "reviewer",
            "description": "Review changes",
            "systemPrompt": "Review carefully.",
            "whenToUse": "Use for code review",
            "toolsAllow": ["read_file", "web_search"],
            "skills": ["debug"],
            "model": {"provider": "openai", "modelId": "gpt-test"},
            "maxTurns": 4,
        },
        None,
        None,
    )
    updated = await tool.execute(
        "agents-2",
        {
            "action": "update",
            "name": "reviewer",
            "systemPrompt": "Review carefully and mention tests.",
            "skills": [],
            "permissionMode": "readonly",
        },
        None,
        None,
    )
    await tool.execute("agents-3", {"action": "delete", "name": "reviewer"}, None, None)

    deleted = await store.get_resource("agent_definition", "reviewer", include_deleted=True)
    created_definition = AgentDefinition.model_validate(created.details["agent"]["definition"])
    updated_definition = AgentDefinition.model_validate(updated.details["agent"]["definition"])

    assert created.details["agent"]["resource"]["version"] == 1
    assert created_definition.system_prompt == "Review carefully."
    assert created_definition.tools_allow == ["read_file", "web_search"]
    assert created_definition.model is not None
    assert updated.details["agent"]["resource"]["version"] == 2
    assert updated_definition.system_prompt == "Review carefully and mention tests."
    assert updated_definition.tools_allow == ["read_file", "web_search"]
    assert updated_definition.skills == []
    assert updated_definition.permission_mode == "readonly"
    assert await store.get_resource("agent_definition", "reviewer") is None
    assert deleted is not None
    assert deleted.deleted_at is not None


@pytest.mark.asyncio
async def test_agents_tool_validates_action_payloads() -> None:
    tool = create_agents_tool(MemoryResourceStore())

    with pytest.raises(ValueError, match="name is required"):
        await tool.execute("agents-1", {"action": "read"}, None, None)
    with pytest.raises(ValueError, match="description is required"):
        await tool.execute(
            "agents-2",
            {"action": "create", "name": "reviewer", "systemPrompt": "Review."},
            None,
            None,
        )
    with pytest.raises(ValueError, match="systemPrompt is required"):
        await tool.execute(
            "agents-3",
            {"action": "create", "name": "reviewer", "description": "Review"},
            None,
            None,
        )
    with pytest.raises(ValueError, match="at least one editable field"):
        await tool.execute("agents-4", {"action": "update", "name": "reviewer"}, None, None)
    with pytest.raises(ResourceNotFoundError, match="Unknown agent definition"):
        await tool.execute(
            "agents-5",
            {"action": "update", "name": "missing", "description": "Missing"},
            None,
            None,
        )


@pytest.mark.asyncio
async def test_agents_tool_read_only_store_write_actions_return_errors() -> None:
    store = ReadOnlyResourceStore(
        MemoryResourceStore([_agent_resource("reviewer", "Review carefully.", "Review")])
    )
    tool = create_agents_tool(store)

    listed = await tool.execute("agents-1", {"action": "list"}, None, None)
    assert listed.details["agents"][0]["name"] == "reviewer"

    with pytest.raises(ResourceReadOnlyError):
        await tool.execute(
            "agents-2",
            {
                "action": "create",
                "name": "new",
                "description": "New agent",
                "systemPrompt": "Act carefully.",
            },
            None,
            None,
        )
    with pytest.raises(ResourceReadOnlyError):
        await tool.execute(
            "agents-3",
            {"action": "update", "name": "reviewer", "description": "New desc"},
            None,
            None,
        )
    with pytest.raises(ResourceReadOnlyError):
        await tool.execute("agents-4", {"action": "delete", "name": "reviewer"}, None, None)


def test_agents_tool_schema_and_optional_registry() -> None:
    store = MemoryResourceStore()
    tool = create_agents_tool(store)

    validate_tool_arguments(
        tool,
        ToolCall(
            id="agents-1",
            name="agents",
            arguments={
                "action": "create",
                "name": "reviewer",
                "description": "Review",
                "systemPrompt": "Review carefully.",
                "thinkingLevel": "off",
            },
        ),
    )
    with pytest.raises(ValueError, match="action"):
        validate_tool_arguments(
            tool,
            ToolCall(id="agents-2", name="agents", arguments={"action": "rename"}),
        )
    with pytest.raises(ValueError, match="thinkingLevel"):
        validate_tool_arguments(
            tool,
            ToolCall(
                id="agents-3",
                name="agents",
                arguments={
                    "action": "create",
                    "name": "reviewer",
                    "description": "Review",
                    "systemPrompt": "Review carefully.",
                    "thinkingLevel": "large",
                },
            ),
        )

    base = create_base_tool_registry()
    with_agents = create_base_tool_registry(agents_store=store)

    assert AGENTS_TOOL_NAME not in base.names()
    assert AGENTS_TOOL_NAME in with_agents.names()
