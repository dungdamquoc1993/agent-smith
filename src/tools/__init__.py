"""Built-in tool factories for Agent Smith agents."""

from tools.ask_user import (
    ASK_USER_QUESTION_TOOL_NAME,
    AskUserQuestionHandler,
    AskUserQuestionRequest,
    AskUserQuestionResponse,
    QuestionAnnotation,
    QuestionOption,
    UserQuestion,
    create_ask_user_question_tool,
)
from tools.manage_resources import (
    MANAGE_RESOURCES_TOOL_NAME,
    ManageResourcesToolInput,
    create_manage_resources_tool,
)
from tools.skill import SKILL_TOOL_NAME, SkillToolInput, create_skill_tool
from tools.sleep import SLEEP_TOOL_NAME, create_sleep_tool
from tools.task import TASK_TOOL_NAME, TaskToolInput, create_task_tool
from tools.task_output import (
    TASK_OUTPUT_TOOL_NAME,
    TaskOutputToolInput,
    create_task_output_tool,
)
from tools.task_stop import TASK_STOP_TOOL_NAME, TaskStopToolInput, create_task_stop_tool
from tools.todo import TODO_WRITE_TOOL_NAME, TodoItem, TodoWriteInput, create_todo_write_tool
from tools.utils import create_base_tool_registry
from tools.web_fetch import WEB_FETCH_TOOL_NAME, WebFetchResponse, create_web_fetch_tool
from tools.web_search import (
    WEB_SEARCH_PROVIDER_ENV,
    WEB_SEARCH_TOOL_NAME,
    BraveSearchProvider,
    SearchProvider,
    SearchProviderRegistry,
    SearchRequest,
    SearchResult,
    TavilySearchProvider,
    create_web_search_tool,
)

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
