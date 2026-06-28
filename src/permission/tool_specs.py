"""Predefined tool permission specs."""

from permission.types import ToolPermissionSpec

READ_ONLY_ALLOW = ToolPermissionSpec(default="allow", read_only=True)
MUTATING_ASK = ToolPermissionSpec(default="ask", mutates_files=True)
INTERACTIVE_ASK = ToolPermissionSpec(
    default="ask",
    requires_user_interaction=True,
)
TASK_ASK = ToolPermissionSpec(default="ask")
MCP_ASK = ToolPermissionSpec(default="ask")
