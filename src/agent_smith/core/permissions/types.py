"""Permission layer types."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from agent_smith.core.llm.types import JsonObject, MaybeAwaitable

PermissionBehavior = Literal["allow", "deny", "ask"]
PermissionMode = Literal["default", "read_only", "accept_edits", "bypass"]
PermissionModeInput = PermissionMode | Literal["plan", "readonly"]
RuleScope = Literal["session", "user", "project", "builtin"]

PERMISSION_MODE_ALIASES: dict[str, PermissionMode] = {
    "plan": "read_only",
    "readonly": "read_only",
}


def normalize_permission_mode(value: str | None, default: str = "default") -> PermissionMode:
    resolved = value or default
    normalized = PERMISSION_MODE_ALIASES.get(resolved, resolved)
    if normalized not in {"default", "read_only", "accept_edits", "bypass"}:
        raise ValueError(f"Unknown permission mode: {resolved}")
    return normalized  # type: ignore[return-value]

SCOPE_PRECEDENCE: dict[RuleScope, int] = {
    "session": 0,
    "user": 1,
    "project": 2,
    "builtin": 3,
}


class ToolPermissionSpec(BaseModel):
    default: PermissionBehavior = "ask"
    read_only: bool = False
    mutates_files: bool = False
    requires_user_interaction: bool = False
    hard_denyable: bool = True

    model_config = {"populate_by_name": True}


class PermissionRule(BaseModel):
    pattern: str
    behavior: PermissionBehavior
    scope: RuleScope = "session"
    session_id: str | None = Field(default=None, alias="sessionId")

    model_config = {"populate_by_name": True}


class PermissionRequest(BaseModel):
    tool_name: str = Field(alias="toolName")
    tool_call_id: str = Field(alias="toolCallId")
    input: JsonObject
    mode: PermissionModeInput = "default"
    is_background: bool = Field(default=False, alias="isBackground")
    tool_spec: ToolPermissionSpec = Field(default_factory=ToolPermissionSpec, alias="toolSpec")
    message: str | None = None
    session_id: str | None = Field(default=None, alias="sessionId")

    model_config = {"populate_by_name": True}


class PermissionDecision(BaseModel):
    behavior: PermissionBehavior
    updated_input: JsonObject | None = Field(default=None, alias="updatedInput")
    reason: str | None = None
    message: str | None = None
    source: str | None = None
    persist_rule: PermissionRule | None = Field(default=None, alias="persistRule")

    model_config = {"populate_by_name": True}

    @classmethod
    def allow(
        cls,
        *,
        updated_input: JsonObject | None = None,
        source: str | None = None,
        persist_rule: PermissionRule | None = None,
    ) -> PermissionDecision:
        return cls(
            behavior="allow",
            updated_input=updated_input,
            source=source,
            persist_rule=persist_rule,
        )

    @classmethod
    def deny(cls, *, reason: str, source: str | None = None) -> PermissionDecision:
        return cls(behavior="deny", reason=reason, source=source)

    @classmethod
    def ask(
        cls,
        *,
        message: str | None = None,
        updated_input: JsonObject | None = None,
        source: str | None = None,
    ) -> PermissionDecision:
        return cls(
            behavior="ask",
            message=message,
            updated_input=updated_input,
            source=source,
        )


CheckPermissionsFn = Callable[[JsonObject], MaybeAwaitable[PermissionDecision | None]]
CanUseTool = Callable[[PermissionRequest], MaybeAwaitable[PermissionDecision]]
