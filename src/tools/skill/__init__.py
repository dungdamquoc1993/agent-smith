"""Skill tool package."""

from __future__ import annotations

from typing import Any

from tools.skill.constants import SKILL_TOOL_NAME

__all__ = ["SKILL_TOOL_NAME", "SkillToolInput", "create_skill_tool"]


def __getattr__(name: str) -> Any:
    if name == "SkillToolInput":
        from tools.skill.tool import SkillToolInput

        return SkillToolInput
    if name == "create_skill_tool":
        from tools.skill.tool import create_skill_tool

        return create_skill_tool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
