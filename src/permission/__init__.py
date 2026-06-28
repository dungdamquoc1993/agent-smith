"""Permission layer for agent tool execution."""

from permission.resolver import PermissionResolver
from permission.store import (
    InMemoryPermissionRuleStore,
    PermissionRuleStore,
    rule_provider_from_store,
)
from permission.types import (
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
