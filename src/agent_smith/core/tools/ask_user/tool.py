"""User-question tool factory."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from agent_smith.core.agent.types import AbortSignal, AgentTool
from agent_smith.core.llm.types import HookPayload, JsonObject
from agent_smith.core.permissions.tool_specs import INTERACTIVE_ASK
from agent_smith.core.permissions.types import PermissionDecision
from agent_smith.core.tools.ask_user.constants import ASK_USER_QUESTION_TOOL_NAME
from agent_smith.core.tools.shared.common import MaybeAwaitable, await_with_abort, text_result


class QuestionOption(BaseModel):
    label: str
    description: str
    preview: str | None = None


class UserQuestion(BaseModel):
    question: str
    header: str
    options: list[QuestionOption] = Field(min_length=2, max_length=4)
    multi_select: bool = Field(default=False, alias="multiSelect")

    model_config = {"populate_by_name": True}


class QuestionAnnotation(BaseModel):
    preview: str | None = None
    notes: str | None = None


class AskUserQuestionRequest(BaseModel):
    tool_call_id: str = Field(alias="toolCallId")
    questions: list[UserQuestion] = Field(min_length=1, max_length=4)
    metadata: dict[str, HookPayload] | None = None

    model_config = {"populate_by_name": True}


class AskUserQuestionResponse(BaseModel):
    answers: dict[str, str]
    annotations: dict[str, QuestionAnnotation] | None = None


AskUserQuestionHandler = Callable[
    [AskUserQuestionRequest, AbortSignal | None],
    MaybeAwaitable,
]


def create_ask_user_question_tool(
    handler: AskUserQuestionHandler | None = None,
    timeout_seconds: float | None = None,
) -> AgentTool:
    async def check_permissions(tool_input: JsonObject) -> PermissionDecision:
        return PermissionDecision.ask(
            message="Answer questions?",
            updated_input=tool_input,
            source="ask_user",
        )

    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = on_update
        answers = args.get("answers")
        if isinstance(answers, dict) and answers:
            answer_parts = [f'"{question}"="{answer}"' for question, answer in answers.items()]
            details: dict[str, HookPayload] = {
                "questions": args.get("questions", []),
                "answers": dict(answers),
            }
            annotations = args.get("annotations")
            if annotations:
                details["annotations"] = annotations
            return text_result(
                "User answered questions: "
                + ", ".join(answer_parts)
                + ". Continue with these answers in mind.",
                details=details,
            )

        if handler is None:
            raise RuntimeError("ask_user_question is not configured with a handler")

        request = AskUserQuestionRequest.model_validate(
            {
                "toolCallId": tool_call_id,
                "questions": args["questions"],
                "metadata": args.get("metadata"),
            }
        )
        raw_response = handler(request, signal)
        response = AskUserQuestionResponse.model_validate(
            await await_with_abort(
                raw_response,
                signal=signal,
                timeout_seconds=timeout_seconds,
            )
        )

        answer_parts = [
            f'"{question}"="{answer}"'
            for question, answer in response.answers.items()
        ]
        details = {
            "questions": [
                question.model_dump(mode="python", by_alias=True)
                for question in request.questions
            ],
            "answers": dict(response.answers),
        }
        if response.annotations:
            details["annotations"] = {
                key: annotation.model_dump(mode="python", exclude_none=True)
                for key, annotation in response.annotations.items()
            }
        return text_result(
            "User answered questions: "
            + ", ".join(answer_parts)
            + ". Continue with these answers in mind.",
            details=details,
        )

    return AgentTool(
        name=ASK_USER_QUESTION_TOOL_NAME,
        label="Ask User Question",
        description=(
            "Ask the user one or more multiple-choice questions and wait for "
            "their answer before continuing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "minLength": 1},
                            "header": {"type": "string", "minLength": 1},
                            "options": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 4,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string", "minLength": 1},
                                        "description": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                        "preview": {"type": "string"},
                                    },
                                    "required": ["label", "description"],
                                    "additionalProperties": False,
                                },
                            },
                            "multiSelect": {"type": "boolean"},
                        },
                        "required": ["question", "header", "options"],
                        "additionalProperties": False,
                    },
                },
                "metadata": {"type": "object"},
                "answers": {
                    "type": "object",
                    "description": "User answers collected by the permission component.",
                    "additionalProperties": {"type": "string"},
                },
                "annotations": {"type": "object"},
            },
            "required": ["questions"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
        permission=INTERACTIVE_ASK,
        check_permissions=check_permissions,
    )
