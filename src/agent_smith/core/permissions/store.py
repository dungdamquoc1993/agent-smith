"""In-memory permission rule store with scope support."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from agent_smith.core.permissions.types import PermissionRule, RuleScope


class PermissionRuleStore(Protocol):
    def list_rules(self, *, visible_session_ids: frozenset[str] | None = None) -> list[PermissionRule]: ...

    def add_rule(self, rule: PermissionRule) -> None: ...


def _rule_key(rule: PermissionRule) -> tuple[str, RuleScope, str | None]:
    if rule.scope == "session":
        return (rule.pattern, rule.scope, rule.session_id)
    return (rule.pattern, rule.scope, None)


class InMemoryPermissionRuleStore:
    def __init__(self, initial_rules: list[PermissionRule] | None = None) -> None:
        self._rules: list[PermissionRule] = list(initial_rules or [])

    def list_rules(self, *, visible_session_ids: frozenset[str] | None = None) -> list[PermissionRule]:
        result: list[PermissionRule] = []
        for rule in self._rules:
            if rule.scope == "session":
                if visible_session_ids is None:
                    continue
                if rule.session_id is None or rule.session_id not in visible_session_ids:
                    continue
            result.append(rule)
        return result

    def add_rule(self, rule: PermissionRule) -> None:
        if rule.scope == "session" and rule.session_id is None:
            raise ValueError("session-scoped permission rules require session_id")
        key = _rule_key(rule)
        self._rules = [existing for existing in self._rules if _rule_key(existing) != key]
        self._rules.append(rule)

    def rules_for_scope(self, scope: RuleScope) -> list[PermissionRule]:
        return [rule for rule in self._rules if rule.scope == scope]


def rule_provider_from_store(
    store: PermissionRuleStore,
    *,
    visible_session_ids: frozenset[str],
) -> Callable[[], list[PermissionRule]]:
    return lambda: store.list_rules(visible_session_ids=visible_session_ids)
