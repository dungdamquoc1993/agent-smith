"""Built-in tool factories for Agent Smith agents."""

from __future__ import annotations

from typing import Any

__all__ = [
    "MANAGE_RESOURCES_TOOL_NAME",
    "TASK_TOOL_NAME",
    "ManageResourcesToolInput",
    "TaskToolInput",
    "ASK_USER_QUESTION_TOOL_NAME",
    "AskUserQuestionHandler",
    "AskUserQuestionRequest",
    "AskUserQuestionResponse",
    "BraveSearchProvider",
    "QuestionAnnotation",
    "QuestionOption",
    "SLEEP_TOOL_NAME",
    "SearchProvider",
    "SearchProviderRegistry",
    "SearchRequest",
    "SearchResult",
    "SKILL_TOOL_NAME",
    "TASK_OUTPUT_TOOL_NAME",
    "TASK_STOP_TOOL_NAME",
    "TODO_WRITE_TOOL_NAME",
    "TavilySearchProvider",
    "TodoItem",
    "TodoWriteInput",
    "SkillToolInput",
    "TaskOutputToolInput",
    "TaskStopToolInput",
    "UserQuestion",
    "WEB_FETCH_TOOL_NAME",
    "WEB_SEARCH_PROVIDER_ENV",
    "WEB_SEARCH_TOOL_NAME",
    "WebFetchResponse",
    "create_manage_resources_tool",
    "create_task_tool",
    "create_ask_user_question_tool",
    "create_base_tool_registry",
    "create_sleep_tool",
    "create_skill_tool",
    "create_task_output_tool",
    "create_task_stop_tool",
    "create_todo_write_tool",
    "create_web_fetch_tool",
    "create_web_search_tool",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ASK_USER_QUESTION_TOOL_NAME": ("tools.ask_user.constants", "ASK_USER_QUESTION_TOOL_NAME"),
    "AskUserQuestionHandler": ("tools.ask_user.tool", "AskUserQuestionHandler"),
    "AskUserQuestionRequest": ("tools.ask_user.tool", "AskUserQuestionRequest"),
    "AskUserQuestionResponse": ("tools.ask_user.tool", "AskUserQuestionResponse"),
    "QuestionAnnotation": ("tools.ask_user.tool", "QuestionAnnotation"),
    "QuestionOption": ("tools.ask_user.tool", "QuestionOption"),
    "UserQuestion": ("tools.ask_user.tool", "UserQuestion"),
    "create_ask_user_question_tool": ("tools.ask_user.tool", "create_ask_user_question_tool"),
    "MANAGE_RESOURCES_TOOL_NAME": ("tools.manage_resources.constants", "MANAGE_RESOURCES_TOOL_NAME"),
    "ManageResourcesToolInput": ("tools.manage_resources.tool", "ManageResourcesToolInput"),
    "create_manage_resources_tool": ("tools.manage_resources.tool", "create_manage_resources_tool"),
    "SKILL_TOOL_NAME": ("tools.skill.constants", "SKILL_TOOL_NAME"),
    "SkillToolInput": ("tools.skill.tool", "SkillToolInput"),
    "create_skill_tool": ("tools.skill.tool", "create_skill_tool"),
    "SLEEP_TOOL_NAME": ("tools.sleep.constants", "SLEEP_TOOL_NAME"),
    "create_sleep_tool": ("tools.sleep.tool", "create_sleep_tool"),
    "TASK_TOOL_NAME": ("tools.task.constants", "TASK_TOOL_NAME"),
    "TaskToolInput": ("tools.task.tool", "TaskToolInput"),
    "create_task_tool": ("tools.task.tool", "create_task_tool"),
    "TASK_OUTPUT_TOOL_NAME": ("tools.task_output.constants", "TASK_OUTPUT_TOOL_NAME"),
    "TaskOutputToolInput": ("tools.task_output.tool", "TaskOutputToolInput"),
    "create_task_output_tool": ("tools.task_output.tool", "create_task_output_tool"),
    "TASK_STOP_TOOL_NAME": ("tools.task_stop.constants", "TASK_STOP_TOOL_NAME"),
    "TaskStopToolInput": ("tools.task_stop.tool", "TaskStopToolInput"),
    "create_task_stop_tool": ("tools.task_stop.tool", "create_task_stop_tool"),
    "TODO_WRITE_TOOL_NAME": ("tools.todo.constants", "TODO_WRITE_TOOL_NAME"),
    "TodoItem": ("tools.todo.tool", "TodoItem"),
    "TodoWriteInput": ("tools.todo.tool", "TodoWriteInput"),
    "create_todo_write_tool": ("tools.todo.tool", "create_todo_write_tool"),
    "create_base_tool_registry": ("tools.registry", "create_base_tool_registry"),
    "WEB_FETCH_TOOL_NAME": ("tools.web_fetch.constants", "WEB_FETCH_TOOL_NAME"),
    "WebFetchResponse": ("tools.web_fetch.tool", "WebFetchResponse"),
    "create_web_fetch_tool": ("tools.web_fetch.tool", "create_web_fetch_tool"),
    "WEB_SEARCH_PROVIDER_ENV": ("tools.web_search.constants", "WEB_SEARCH_PROVIDER_ENV"),
    "WEB_SEARCH_TOOL_NAME": ("tools.web_search.constants", "WEB_SEARCH_TOOL_NAME"),
    "BraveSearchProvider": ("tools.web_search.tool", "BraveSearchProvider"),
    "SearchProvider": ("tools.web_search.tool", "SearchProvider"),
    "SearchProviderRegistry": ("tools.web_search.tool", "SearchProviderRegistry"),
    "SearchRequest": ("tools.web_search.tool", "SearchRequest"),
    "SearchResult": ("tools.web_search.tool", "SearchResult"),
    "TavilySearchProvider": ("tools.web_search.tool", "TavilySearchProvider"),
    "create_web_search_tool": ("tools.web_search.tool", "create_web_search_tool"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, attr_name)
