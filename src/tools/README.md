# Agent Tools

Built-in `AgentTool` factories for Agent Smith agents.

This package contains concrete tools that can be registered with
`runtime.ToolRegistry` and then selected by `AgentFactory` through
`toolsAllow` / `toolsDeny`.

## Factories

| Factory | Tool name | Purpose |
| --- | --- | --- |
| `create_sleep_tool()` | `sleep` | Wait for a bounded duration, with abort support. |
| `create_todo_write_tool()` | `todo_write` | Echo a full stateless todo list for planning/status. |
| `create_ask_user_question_tool()` | `ask_user_question` | Pause tool execution on an injected handler and resume with user answers. |
| `create_web_fetch_tool()` | `web_fetch` | Fetch HTTP/HTTPS content and return extracted text. |
| `create_web_search_tool()` | `web_search` | Search through configured Tavily or Brave providers. |
| `create_skill_tool()` | `skill` | Invoke a skill by name with optional arguments. |
| `create_task_tool()` | `task` | Spawn a named sub-agent task sync or async. |
| `create_manage_resources_tool()` | `manage_resources` | List, load, create, update, or delete catalog resources. |
| `create_task_output_tool()` | `task_output` | Read or wait for task output/result snapshots. |
| `create_task_stop_tool()` | `task_stop` | Stop a running task. |
| `create_base_tool_registry()` | n/a | Convenience helper that assembles the base tool bundle. |

## Registry Assembly

`create_base_tool_registry()` returns the Phase 1 tools by default:

```python
from tools import create_base_tool_registry

tool_registry = create_base_tool_registry(
    ask_user_handler=handler,
    web_search_env=os.environ,
)
```

Resource tools are added when a `ResourceStore` / `ResourceResolver` is provided:

```python
from resources import MemoryResourceStore, ResourceResolver
from tools import create_base_tool_registry

store = MemoryResourceStore()
tool_registry = create_base_tool_registry(
    resources_store=store,
    resources_resolver=ResourceResolver([store]),
)
```

Task tools are added only when `task_runtime` is provided; `task` is added only
when both `task_runtime` and `agent_runner` are provided. Pass
`agent_parent_metadata` to propagate parent session/principal provenance into
sub-agent tasks.

## Resource Behavior

- `todo_write` is intentionally stateless. The caller passes the full list each time.
- `skill` invokes catalog skills resolved through `ResourceResolver`. Available skills
  are surfaced via `<system-reminder>` user messages in the harness.
- `manage_resources` writes through the injected `ResourceStore` and uses
  `ResourceResolver` for resolved `list` / `read` views when provided.
- `task` task metadata includes `parentToolCallId`; child session persistence is
  controlled by the injected `AgentTaskRunner.session_factory`.

## Web Search Configuration

`web_search` supports Tavily and Brave. Provider selection order is:

1. Explicit `provider=` passed to `create_web_search_tool`.
2. `AGENT_SMITH_WEB_SEARCH_PROVIDER=tavily|brave`.
3. First configured provider with credentials.

Environment variables:

```text
TAVILY_API_KEY=
BRAVE_SEARCH_API_KEY=
AGENT_SMITH_WEB_SEARCH_PROVIDER=
```
