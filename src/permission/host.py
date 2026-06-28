"""Host-side permission prompt helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from permission.utils import maybe_await
from permission.types import CanUseTool, PermissionDecision, PermissionRequest


class QueuedCanUseTool:
    """Serialize permission prompts so only one dialog is active at a time."""

    def __init__(self, handler: CanUseTool) -> None:
        self._handler = handler
        self._lock = asyncio.Lock()

    async def __call__(self, request: PermissionRequest) -> PermissionDecision:
        async with self._lock:
            return await maybe_await(self._handler(request))


ToolApprovalHandler = Callable[[PermissionRequest], Awaitable[PermissionDecision]]
AskUserQuestionHandler = Callable[..., Awaitable[object]]


def create_can_use_tool(
    *,
    ask_user_handler: AskUserQuestionHandler | None = None,
    tool_approval_handler: ToolApprovalHandler | None = None,
    serialize_prompts: bool = True,
) -> CanUseTool | None:
    if ask_user_handler is None and tool_approval_handler is None:
        return None

    async def can_use_tool(request: PermissionRequest) -> PermissionDecision:
        from tools.ask_user.constants import ASK_USER_QUESTION_TOOL_NAME
        from tools.ask_user.tool import AskUserQuestionRequest, AskUserQuestionResponse

        if request.tool_name == ASK_USER_QUESTION_TOOL_NAME:
            if ask_user_handler is None:
                return PermissionDecision.deny(
                    reason="ask_user_question is not configured with a handler",
                    source="missing_ask_user_handler",
                )
            ask_request = AskUserQuestionRequest.model_validate(
                {
                    "toolCallId": request.tool_call_id,
                    "questions": request.input.get("questions", []),
                    "metadata": request.input.get("metadata"),
                }
            )
            raw_response = await maybe_await(ask_user_handler(ask_request, None))
            response = (
                raw_response
                if isinstance(raw_response, AskUserQuestionResponse)
                else AskUserQuestionResponse.model_validate(raw_response)
            )
            updated_input = {
                **request.input,
                "answers": dict(response.answers),
            }
            if response.annotations:
                updated_input["annotations"] = {
                    key: annotation.model_dump(mode="python", exclude_none=True)
                    for key, annotation in response.annotations.items()
                }
            return PermissionDecision.allow(
                updated_input=updated_input,
                source="ask_user_handler",
            )

        if tool_approval_handler is None:
            return PermissionDecision.deny(
                reason=f"No approval handler configured for tool {request.tool_name}.",
                source="missing_tool_approval_handler",
            )
        return await tool_approval_handler(request)

    if serialize_prompts:
        return QueuedCanUseTool(can_use_tool)
    return can_use_tool
