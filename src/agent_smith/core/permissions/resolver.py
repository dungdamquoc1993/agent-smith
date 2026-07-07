"""Central permission resolver."""

from __future__ import annotations

import fnmatch
from collections.abc import Callable

from agent_smith.core.permissions.utils import maybe_await
from agent_smith.core.permissions.types import (
    CheckPermissionsFn,
    PermissionBehavior,
    PermissionDecision,
    PermissionModeInput,
    PermissionRequest,
    PermissionRule,
    SCOPE_PRECEDENCE,
    ToolPermissionSpec,
    normalize_permission_mode,
)


def _matches_pattern(pattern: str, tool_name: str) -> bool:
    if ":" in pattern:
        base, _subkey = pattern.split(":", 1)
        return fnmatch.fnmatch(tool_name, base)
    return fnmatch.fnmatch(tool_name, pattern)


def _best_matching_rule(
    rules: list[PermissionRule],
    tool_name: str,
    behavior: PermissionBehavior,
) -> PermissionRule | None:
    matches = [
        rule
        for rule in rules
        if rule.behavior == behavior and _matches_pattern(rule.pattern, tool_name)
    ]
    if not matches:
        return None
    return min(matches, key=lambda rule: SCOPE_PRECEDENCE[rule.scope])


class PermissionResolver:
    def __init__(
        self,
        *,
        rule_provider: Callable[[], list[PermissionRule]] | None = None,
        default_mode: PermissionModeInput = "default",
        hard_deny: list[str] | None = None,
    ) -> None:
        self._rule_provider = rule_provider or (lambda: [])
        self.default_mode = default_mode
        self.hard_deny = list(hard_deny or [])

    def list_rules(self) -> list[PermissionRule]:
        return self._rule_provider()

    async def resolve(
        self,
        request: PermissionRequest,
        *,
        check_permissions: CheckPermissionsFn | None = None,
        mode: PermissionModeInput | None = None,
    ) -> PermissionDecision:
        tool_name = request.tool_name
        tool_spec = request.tool_spec
        resolved_mode = normalize_permission_mode(mode or request.mode, self.default_mode)
        rules = self._rule_provider()

        if self._is_hard_denied(tool_name, tool_spec):
            return PermissionDecision.deny(
                reason=f"Tool {tool_name} is blocked by safety policy.",
                source="hard_deny",
            )

        deny_rule = _best_matching_rule(rules, tool_name, "deny")
        if deny_rule is not None:
            return PermissionDecision.deny(
                reason=f"Denied by rule {deny_rule.pattern!r} ({deny_rule.scope}).",
                source=f"rule:{deny_rule.scope}",
            )

        if resolved_mode == "bypass":
            return PermissionDecision.allow(source="mode:bypass")

        if resolved_mode == "read_only":
            if tool_spec.read_only:
                return PermissionDecision.allow(source="mode:read_only")
            return PermissionDecision.deny(
                reason=f"Permission mode 'read_only' blocks mutating tool {tool_name}.",
                source="mode:read_only",
            )

        allow_rule = _best_matching_rule(rules, tool_name, "allow")
        if allow_rule is not None:
            return PermissionDecision.allow(source=f"rule:{allow_rule.scope}")

        if check_permissions is not None:
            tool_decision = await maybe_await(check_permissions(request.input))
            if tool_decision is not None:
                return tool_decision

        if resolved_mode == "accept_edits" and tool_spec.mutates_files:
            return PermissionDecision.allow(source="mode:accept_edits")

        ask_rule = _best_matching_rule(rules, tool_name, "ask")
        if ask_rule is not None:
            return PermissionDecision.ask(source=f"rule:{ask_rule.scope}")

        default_behavior = tool_spec.default
        if default_behavior == "allow":
            return PermissionDecision.allow(source="tool_default")
        if default_behavior == "deny":
            return PermissionDecision.deny(
                reason=f"Tool {tool_name} denied by default policy.",
                source="tool_default",
            )

        return PermissionDecision.ask(
            message=request.message or f"Allow {tool_name}?",
            source="tool_default",
        )

    def _is_hard_denied(self, tool_name: str, tool_spec: ToolPermissionSpec) -> bool:
        if not tool_spec.hard_denyable:
            return False
        return any(_matches_pattern(pattern, tool_name) for pattern in self.hard_deny)
