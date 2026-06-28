"""Permission layer types."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from ai.types import JsonObject, MaybeAwaitable

PermissionBehavior = Literal["allow", "deny", "ask"]
PermissionMode = Literal["plan", "default", "accept_edits", "bypass"]
RuleScope = Literal["session", "user", "project", "builtin"]

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

    model_config = {"populate_by_name": True}


class PermissionRequest(BaseModel):
    tool_name: str = Field(alias="toolName")
    tool_call_id: str = Field(alias="toolCallId")
    input: JsonObject
    mode: PermissionMode = "default"
    is_background: bool = Field(default=False, alias="isBackground")
    tool_spec: ToolPermissionSpec = Field(default_factory=ToolPermissionSpec, alias="toolSpec")
    message: str | None = None

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
