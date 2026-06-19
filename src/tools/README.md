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
| `create_skills_tool()` | `skills` | List, load, create, update, and delete skill resources. |
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

The `skills` tool is added only when a writable `ResourceStore` is provided:

```python
from resources import MemoryResourceStore, ResourceResolver
from tools import create_base_tool_registry

store = MemoryResourceStore()
tool_registry = create_base_tool_registry(
    skills_store=store,
    skills_resolver=ResourceResolver([store]),
)
```

## Resource Behavior

- `todo_write` is intentionally stateless. The caller passes the full list each time.
- `skills` writes through the injected `ResourceStore` and uses `ResourceResolver`
  for resolved `list` / `read` views when provided.
- `skills.read` returns full skill content in the tool result text so the next model
  turn can use the loaded instructions.
- Filesystem resources are read-only; create/update/delete should use memory,
  Postgres, or another writable `ResourceStore`.

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
