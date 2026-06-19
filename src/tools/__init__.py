"""Built-in tool factories for Agent Smith agents."""

from tools.agent import AGENT_TOOL_NAME, AgentToolInput, create_agent_tool
from tools.agents import AGENTS_TOOL_NAME, AgentsToolInput, create_agents_tool
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
from tools.sleep import SLEEP_TOOL_NAME, create_sleep_tool
from tools.skills import (
    SKILLS_TOOL_NAME,
    SkillsToolInput,
    create_skills_tool,
)
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
    "AGENT_TOOL_NAME",
    "AGENTS_TOOL_NAME",
    "ASK_USER_QUESTION_TOOL_NAME",
    "AgentsToolInput",
    "AgentToolInput",
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
    "SKILLS_TOOL_NAME",
    "TASK_OUTPUT_TOOL_NAME",
    "TASK_STOP_TOOL_NAME",
    "TODO_WRITE_TOOL_NAME",
    "TavilySearchProvider",
    "TodoItem",
    "TodoWriteInput",
    "SkillsToolInput",
    "TaskOutputToolInput",
    "TaskStopToolInput",
    "UserQuestion",
    "WEB_FETCH_TOOL_NAME",
    "WEB_SEARCH_PROVIDER_ENV",
    "WEB_SEARCH_TOOL_NAME",
    "WebFetchResponse",
    "create_agent_tool",
    "create_agents_tool",
    "create_ask_user_question_tool",
    "create_base_tool_registry",
    "create_sleep_tool",
    "create_skills_tool",
    "create_task_output_tool",
    "create_task_stop_tool",
    "create_todo_write_tool",
    "create_web_fetch_tool",
    "create_web_search_tool",
]
