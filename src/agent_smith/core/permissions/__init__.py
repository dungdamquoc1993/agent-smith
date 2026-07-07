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
    PermissionModeInput,
    PermissionRequest,
    PermissionRule,
    RuleScope,
    SCOPE_PRECEDENCE,
    ToolPermissionSpec,
    normalize_permission_mode,
)

__all__ = [
    "CanUseTool",
    "CheckPermissionsFn",
    "InMemoryPermissionRuleStore",
    "PermissionBehavior",
    "PermissionDecision",
    "PermissionMode",
    "PermissionModeInput",
    "PermissionRequest",
    "PermissionResolver",
    "PermissionRule",
    "PermissionRuleStore",
    "RuleScope",
    "SCOPE_PRECEDENCE",
    "ToolPermissionSpec",
    "normalize_permission_mode",
    "rule_provider_from_store",
]
