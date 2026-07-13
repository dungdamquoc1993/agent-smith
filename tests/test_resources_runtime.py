from __future__ import annotations

import uuid
from os import getenv

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_smith.core.agent import AgentTool, AgentToolResult
from agent_smith.core.llm.models import make_litellm_model
from agent_smith.core.llm.types import TextContent
from agent_smith.infra.db.base import Base
from agent_smith.core.resources import (
    AgentDefinition,
    ResourceConflictError,
    ResourceCreate,
    ResourceNotFoundError,
    ResourceResolver,
)
from agent_smith.infra.persistence.postgres_resources import PostgresResourceStore
from agent_smith.core.runtime import AgentFactory, AgentFactoryError, ToolRegistry
from helpers.resource_stores import MemoryResourceStore
from helpers.sessions import MemorySessionRepo


def _skill_content(name: str, body: str) -> dict:
    return {
        "name": name,
        "description": f"{name} skill",
        "content": body,
        "filePath": f"/tmp/{name}/SKILL.md",
    }


def _agent_content(name: str = "reviewer") -> dict:
    return {
        "name": name,
        "description": "Review changes",
        "systemPrompt": "Review carefully.",
        "toolsAllow": ["read_file", "write_file"],
        "toolsDeny": ["write_file"],
        "skills": ["debug"],
    }


def _tool(name: str) -> AgentTool:
    def execute(tool_call_id, args, signal, update):
        _ = tool_call_id, args, signal, update
        return AgentToolResult(content=[TextContent(text="ok")])

    return AgentTool(
        name=name,
        label=name,
        description=f"{name} tool",
        parameters={"type": "object", "properties": {}},
        execute=execute,
    )


@pytest.mark.asyncio
async def test_memory_resource_store_crud_versions_disable_and_delete() -> None:
    store = MemoryResourceStore()
    created = await store.create_resource(
        ResourceCreate(
            kind="skill",
            name="debug",
            content=_skill_content("debug", "Read logs."),
            description="Debug problems",
        )
    )

    updated = await store.update_resource(
        "skill",
        "debug",
        {
            "content": _skill_content("debug", "Read logs and traces."),
            "disabled": True,
        },
    )

    assert updated.current_version.version == 2
    assert updated.current_version.content_hash != created.current_version.content_hash
    assert (await ResourceResolver([store]).resolve()).harness_resources.skills == []

    await store.update_resource("skill", "debug", {"disabled": False})
    assert len((await ResourceResolver([store]).resolve()).harness_resources.skills or []) == 1

    await store.delete_resource("skill", "debug")
    assert await store.get_resource("skill", "debug") is None
    deleted = await store.get_resource("skill", "debug", include_deleted=True)
    assert deleted is not None
    assert deleted.deleted_at is not None
    with pytest.raises(ResourceNotFoundError):
        await store.delete_resource("skill", "debug")


@pytest.mark.asyncio
async def test_resource_resolver_priority_and_runtime_mapping() -> None:
    base = MemoryResourceStore(
        [
            {
                "kind": "skill",
                "name": "debug",
                "content": _skill_content("debug", "base"),
                "scope": "project",
            },
            {
                "kind": "prompt_template",
                "name": "fix",
                "content": {"name": "fix", "content": "Fix $1"},
            },
            {
                "kind": "agent_definition",
                "name": "reviewer",
                "content": _agent_content(),
            },
            {
                "kind": "mcp_server_config",
                "name": "github",
                "content": {"name": "github", "config": {"command": "github-mcp"}},
            },
            {
                "kind": "user_memory",
                "name": "default",
                "content": {"content": "Base memory."},
                "scope": "project",
            },
        ]
    )
    override = MemoryResourceStore(
        [
            {
                "kind": "skill",
                "name": "debug",
                "content": _skill_content("debug", "override"),
                "scope": "user",
            },
            {
                "kind": "user_memory",
                "name": "default",
                "content": {"content": "Override memory."},
                "scope": "user",
            }
        ]
    )

    resolved = await ResourceResolver([base, override]).resolve()

    assert resolved.harness_resources.skills is not None
    assert len(resolved.harness_resources.skills) == 1
    assert resolved.harness_resources.skills[0].content == "override"
    assert resolved.harness_resources.prompt_templates is not None
    assert resolved.harness_resources.prompt_templates[0].name == "fix"
    assert resolved.harness_resources.user_memory is not None
    assert resolved.harness_resources.user_memory.content == "Override memory."
    assert resolved.agent_definitions[0].name == "reviewer"
    assert resolved.mcp_server_configs["github"] == {"command": "github-mcp"}


@pytest.mark.asyncio
async def test_resource_resolver_skips_disabled_user_memory() -> None:
    store = MemoryResourceStore(
        [
            {
                "kind": "user_memory",
                "name": "default",
                "content": {"content": "Disabled memory."},
                "disabled": True,
            }
        ]
    )

    resolved = await ResourceResolver([store]).resolve()

    assert resolved.harness_resources.user_memory is None

    deleted_store = MemoryResourceStore(
        [
            {
                "kind": "user_memory",
                "name": "default",
                "content": {"content": "Deleted memory."},
            }
        ]
    )
    await deleted_store.delete_resource("user_memory", "default")

    deleted_resolved = await ResourceResolver([deleted_store]).resolve()

    assert deleted_resolved.harness_resources.user_memory is None


@pytest.mark.asyncio
async def test_agent_factory_compiles_definition_into_harness_options() -> None:
    store = MemoryResourceStore(
        [
            {
                "kind": "skill",
                "name": "debug",
                "content": _skill_content("debug", "Use logs."),
            },
            {
                "kind": "agent_definition",
                "name": "reviewer",
                "content": _agent_content(),
            },
            {
                "kind": "user_memory",
                "name": "default",
                "content": {"content": "Factory memory."},
            },
        ]
    )
    factory = AgentFactory(
        resource_resolver=ResourceResolver([store]),
        tool_registry=ToolRegistry([_tool("read_file"), _tool("write_file")]),
        default_model=make_litellm_model(provider="openai", model_id="gpt-test"),
    )
    session = await MemorySessionRepo().create(principal_id="principal-1")

    options = await factory.create_options("reviewer", session=session)

    assert options.system_prompt == "Review carefully."
    assert options.active_tool_names == ["read_file"]
    assert [tool.name for tool in options.tools or []] == ["read_file"]
    assert options.resources is not None
    assert [skill.name for skill in options.resources.skills or []] == ["debug"]
    assert options.resources.user_memory is not None
    assert options.resources.user_memory.content == "Factory memory."


@pytest.mark.asyncio
async def test_agent_factory_validates_missing_tool_and_skill() -> None:
    store = MemoryResourceStore(
        [
            {
                "kind": "skill",
                "name": "debug",
                "content": _skill_content("debug", "Use logs."),
            }
        ]
    )
    factory = AgentFactory(
        resource_resolver=ResourceResolver([store]),
        tool_registry=ToolRegistry([_tool("read_file")]),
        default_model=make_litellm_model(provider="openai", model_id="gpt-test"),
    )

    with pytest.raises(AgentFactoryError, match="Unknown tool"):
        await factory.build_runtime_spec(
            AgentDefinition(
                name="bad-tools",
                description="bad",
                system_prompt="bad",
                tools_allow=["missing"],
            )
        )

    with pytest.raises(AgentFactoryError, match="Unknown skill"):
        await factory.build_runtime_spec(
            AgentDefinition(
                name="bad-skill",
                description="bad",
                system_prompt="bad",
                skills=["missing"],
            )
        )


@pytest.mark.asyncio
async def test_postgres_resource_store_roundtrip_when_database_is_configured() -> None:
    postgres_url = getenv("AGENT_SMITH_TEST_POSTGRES_URL")
    if not postgres_url:
        pytest.skip("AGENT_SMITH_TEST_POSTGRES_URL is not configured")

    engine = create_async_engine(postgres_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = PostgresResourceStore(factory)
        suffix = uuid.uuid4().hex
        skill_name = f"debug_{suffix}"
        prompt_name = f"fix_{suffix}"
        agent_name = f"reviewer_{suffix}"
        mcp_name = f"github_{suffix}"

        created = await store.create_resource(
            {
                "kind": "skill",
                "name": skill_name,
                "content": _skill_content(skill_name, "Read logs."),
                "description": "Debug problems",
            }
        )
        assert created.source_type == "postgres"
        assert created.scope == "user"
        assert (await store.get_resource("skill", skill_name)) is not None
        assert [record.name for record in await store.list_resources(kind="skill") if record.name == skill_name] == [
            skill_name
        ]

        with pytest.raises(ResourceConflictError):
            await store.create_resource(
                {
                    "kind": "skill",
                    "name": skill_name,
                    "content": _skill_content(skill_name, "Duplicate."),
                }
            )

        updated = await store.update_resource(
            "skill",
            skill_name,
            {
                "content": _skill_content(skill_name, "Read logs and traces."),
                "disabled": True,
            },
        )
        assert updated.current_version.version == 2
        assert updated.current_version.content_hash != created.current_version.content_hash
        assert [record.name for record in await ResourceResolver([store]).list_records("skill") if record.name == skill_name] == []

        await store.update_resource("skill", skill_name, {"disabled": False})
        await store.create_resource(
            {
                "kind": "prompt_template",
                "name": prompt_name,
                "content": {"name": prompt_name, "content": "Fix $1"},
            }
        )
        await store.create_resource(
            {
                "kind": "agent_definition",
                "name": agent_name,
                "content": {
                    **_agent_content(agent_name),
                    "skills": [skill_name],
                    "promptTemplates": [prompt_name],
                },
            }
        )
        await store.create_resource(
            {
                "kind": "mcp_server_config",
                "name": mcp_name,
                "content": {"name": mcp_name, "config": {"command": "github-mcp"}},
            }
        )

        resolved = await ResourceResolver([store]).resolve()
        assert any(skill.name == skill_name for skill in resolved.harness_resources.skills or [])
        assert any(
            template.name == prompt_name
            for template in resolved.harness_resources.prompt_templates or []
        )
        assert any(definition.name == agent_name for definition in resolved.agent_definitions)
        assert resolved.mcp_server_configs[mcp_name] == {"command": "github-mcp"}

        await store.delete_resource("skill", skill_name)
        assert await store.get_resource("skill", skill_name) is None
        deleted = await store.get_resource("skill", skill_name, include_deleted=True)
        assert deleted is not None
        assert deleted.deleted_at is not None
        with pytest.raises(ResourceNotFoundError):
            await store.update_resource("skill", "missing", {"disabled": True})
        with pytest.raises(ResourceNotFoundError):
            await store.delete_resource("skill", skill_name)
    finally:
        await engine.dispose()
