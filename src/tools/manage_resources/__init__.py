"""Manage resources tool package."""

from __future__ import annotations

from typing import Any

from tools.manage_resources.constants import MANAGE_RESOURCES_TOOL_NAME

__all__ = ["MANAGE_RESOURCES_TOOL_NAME", "ManageResourcesToolInput", "create_manage_resources_tool"]


def __getattr__(name: str) -> Any:
    if name == "ManageResourcesToolInput":
        from tools.manage_resources.tool import ManageResourcesToolInput

        return ManageResourcesToolInput
    if name == "create_manage_resources_tool":
        from tools.manage_resources.tool import create_manage_resources_tool

        return create_manage_resources_tool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
