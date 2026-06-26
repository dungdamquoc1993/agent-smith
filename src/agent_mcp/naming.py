"""Naming helpers for MCP-backed Agent Smith tools."""

from __future__ import annotations

import re

_INVALID_NAME_CHARS = re.compile(r"[^a-z0-9_]+")
_REPEATED_UNDERSCORES = re.compile(r"_+")


def normalize_mcp_name(name: str) -> str:
    normalized = _INVALID_NAME_CHARS.sub("_", name.strip().lower())
    normalized = _REPEATED_UNDERSCORES.sub("_", normalized).strip("_")
    if not normalized:
        raise ValueError(f"MCP name normalizes to empty: {name!r}")
    return normalized


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    return f"mcp__{normalize_mcp_name(server_name)}__{normalize_mcp_name(tool_name)}"


def mcp_tool_prefix(server_name: str) -> str:
    return f"mcp__{normalize_mcp_name(server_name)}__"
