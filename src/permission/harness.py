"""Harness integration helpers for permission resolution."""

from __future__ import annotations

from permission.utils import maybe_await
from agent.types import AgentTool
from permission.resolver import PermissionResolver
from permission.store import PermissionRuleStore
from permission.types import CanUseTool, PermissionDecision, PermissionMode, PermissionRequest, PermissionRule


def _coerce_input(args: object) -> dict:
    if isinstance(args, dict):
        return args
    return {"value": args}


def _prepare_persist_rule(rule: PermissionRule, session_id: str | None) -> PermissionRule | None:
    if rule.scope != "session":
        return rule
    if rule.session_id is not None:
        return rule
    if session_id is None:
        return None
    return rule.model_copy(update={"session_id": session_id})


async def resolve_harness_tool_permission(
    *,
    tool: AgentTool,
    tool_call_id: str,
    args: object,
    permission_mode: PermissionMode,
    is_background: bool,
    permission_resolver: PermissionResolver | None,
    can_use_tool: CanUseTool | None,
    permission_rule_store: PermissionRuleStore | None = None,
    session_id: str | None = None,
) -> PermissionDecision | None:
    if permission_resolver is None:
        return None

    tool_input = _coerce_input(args)
    request = PermissionRequest(
        tool_name=tool.name,
        tool_call_id=tool_call_id,
        input=tool_input,
        mode=permission_mode,
        is_background=is_background,
        tool_spec=tool.permission,
        session_id=session_id,
    )
    decision = await permission_resolver.resolve(
        request,
        check_permissions=tool.check_permissions,
        mode=permission_mode,
    )

    if decision.behavior == "ask":
        if is_background:
            return PermissionDecision.deny(
                reason=(
                    f"Permission prompts are not available for background agent "
                    f"tool {tool.name}."
                ),
                source="headless",
            )
        if can_use_tool is None:
            return PermissionDecision.deny(
                reason=f"No permission handler configured for tool {tool.name}.",
                source="missing_can_use_tool",
            )
        decision = await maybe_await(can_use_tool(request))
        if decision.persist_rule is not None and permission_rule_store is not None:
            prepared = _prepare_persist_rule(decision.persist_rule, session_id)
            if prepared is not None:
                permission_rule_store.add_rule(prepared)

    return decision


def permission_decision_to_before_result(
    decision: PermissionDecision,
) -> dict[str, object] | None:
    if decision.behavior == "deny":
        return {
            "block": True,
            "reason": decision.reason or "Tool execution was denied.",
        }
    if decision.behavior == "allow":
        if decision.updated_input is not None:
            return {"updatedArgs": decision.updated_input}
        return None
    if decision.behavior == "ask":
        return {
            "block": True,
            "reason": decision.reason or "Tool execution was not approved.",
        }
    return None
