"""Permission layer for agent tool execution."""

from agent_smith.core.permissions.resolver import PermissionResolver
from agent_smith.core.permissions.store import (
    InMemoryPermissionRuleStore,
    PermissionRuleStore,
    rule_provider_from_store,
)
from agent_smith.core.permissions.types import (
    CanUseTool,
    CheckPermissionsFn,
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
    PermissionRequest,
    PermissionRule,
    RuleScope,
    SCOPE_PRECEDENCE,
    ToolPermissionSpec,
)

__all__ = [
    "CanUseTool",
    "CheckPermissionsFn",
    "InMemoryPermissionRuleStore",
    "PermissionBehavior",
    "PermissionDecision",
    "PermissionMode",
    "PermissionRequest",
    "PermissionResolver",
    "PermissionRule",
    "PermissionRuleStore",
    "RuleScope",
    "SCOPE_PRECEDENCE",
    "ToolPermissionSpec",
    "rule_provider_from_store",
]
