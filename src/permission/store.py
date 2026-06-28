"""In-memory permission rule store with scope support."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from permission.types import PermissionRule, RuleScope


class PermissionRuleStore(Protocol):
    def list_rules(self) -> list[PermissionRule]: ...

    def add_rule(self, rule: PermissionRule) -> None: ...


class InMemoryPermissionRuleStore:
    def __init__(self, initial_rules: list[PermissionRule] | None = None) -> None:
        self._rules: list[PermissionRule] = list(initial_rules or [])

    def list_rules(self) -> list[PermissionRule]:
        return list(self._rules)

    def add_rule(self, rule: PermissionRule) -> None:
        self._rules = [
            existing
            for existing in self._rules
            if not (existing.pattern == rule.pattern and existing.scope == rule.scope)
        ]
        self._rules.append(rule)

    def rules_for_scope(self, scope: RuleScope) -> list[PermissionRule]:
        return [rule for rule in self._rules if rule.scope == scope]


def rule_provider_from_store(store: PermissionRuleStore) -> Callable[[], list[PermissionRule]]:
    return store.list_rules
