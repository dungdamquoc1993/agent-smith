"""MCP runtime exceptions."""

from __future__ import annotations


class McpRuntimeError(Exception):
    pass


class McpAuthRequiredError(McpRuntimeError):
    pass


class McpCredentialError(McpRuntimeError):
    pass
