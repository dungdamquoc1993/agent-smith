from __future__ import annotations

import pytest

from agent_smith.core.resources import ResourceNotFoundError, ResourceResolver
from agent_smith.core.tools import create_skill_tool
from helpers.resource_stores import MemoryResourceStore


def _skill_resource(name: str, content: str, *, disable_model_invocation: bool | None = None) -> dict:
    payload = {
        "kind": "skill",
        "name": name,
        "description": f"{name} skill",
        "content": {
            "name": name,
            "description": f"{name} skill",
            "content": content,
            "filePath": f"/skills/{name}/SKILL.md",
        },
    }
    if disable_model_invocation is not None:
        payload["content"]["disableModelInvocation"] = disable_model_invocation
    return payload


@pytest.mark.asyncio
async def test_skill_tool_invokes_resolved_skill() -> None:
    base = MemoryResourceStore([_skill_resource("debug", "Use base logs.")])
    override = MemoryResourceStore([_skill_resource("debug", "Use override traces.")])
    tool = create_skill_tool(resolver=ResourceResolver([base, override]))

    result = await tool.execute("skill-1", {"skill": "debug"}, None, None)

    assert "Use override traces." in result.content[0].text
    assert result.details["skill"] == "debug"


@pytest.mark.asyncio
async def test_skill_tool_strips_leading_slash_and_applies_args() -> None:
    store = MemoryResourceStore(
        [_skill_resource("commit", "Commit with message: $ARGUMENTS")]
    )
    tool = create_skill_tool(resolver=ResourceResolver([store]))

    result = await tool.execute("skill-1", {"skill": "/commit", "args": "-m fix"}, None, None)

    assert "Commit with message: -m fix" in result.content[0].text


@pytest.mark.asyncio
async def test_skill_tool_rejects_missing_disabled_and_non_invocable() -> None:
    store = MemoryResourceStore(
        [
            _skill_resource("hidden", "Secret", disable_model_invocation=True),
        ]
    )
    tool = create_skill_tool(resolver=ResourceResolver([store]))

    with pytest.raises(ResourceNotFoundError, match="Unknown skill"):
        await tool.execute("skill-1", {"skill": "missing"}, None, None)
    with pytest.raises(ValueError, match="cannot be invoked"):
        await tool.execute("skill-2", {"skill": "hidden"}, None, None)
