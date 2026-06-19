# Changelog

Lich su thay doi implementation cua Agent Smith (code, khong phai design notes).

Design notes van nam trong [agent-smith-idea/](agent-smith-idea/).

---

## [Unreleased]

### Added - Agent task runtime and agent config tools

- Them package [`tasks`](../src/tasks/) lam runtime core in-memory cho background/sub-agent work: `MemoryTaskRuntime`, `TaskRecord`, `TaskContext`, typed errors va `MemoryTaskOutputStore`.
- Them [`AgentTaskRunner`](../src/tasks/runners/agent.py) de chay sub-agent qua `AgentFactory` voi child `AgentHarnessSession`, recursion guard, abort propagation va result metadata.
- Them runtime task tools trong [`tools`](../src/tools/): `agent` de spawn/chay sub-agent sync/async, `task_output` de doc/cho output, va `task_stop` de dung task dang chay.
- Them `agents` tool de list/read/create/update/delete `agent_definition` resources, tach rieng khoi runtime spawn tool `agent`.
- Mo rong `create_base_tool_registry(...)` de optional-register `agents`, `agent`, `task_output`, va `task_stop` khi caller truyen store/runtime/runner tuong ung.
- Them root overview [`PHASE3_AGENT_TASK_RUNTIME.md`](../PHASE3_AGENT_TASK_RUNTIME.md) de doc nhanh luong implementation va cac file lien quan.

### Added - Agent task runtime tests

- Them tests cho task runtime lifecycle, agent runner, runtime task tools, va agent config CRUD.
- Full local suite hien tai: `poetry run pytest` -> `79 passed, 2 skipped`.

### Added - Base agent tools

- Them package [`tools`](../src/tools/) gom cac `AgentTool` factory co the register qua `ToolRegistry`: `sleep`, `todo_write`, `ask_user_question`, `web_fetch`, `web_search`, va `skills`.
- Them helper `create_base_tool_registry(...)` de lap base tool bundle; mac dinh giu 5 tool stateless/base va chi them `skills` khi caller truyen `skills_store`.
- Them `ask_user_question` theo pause/resume qua injected handler: tool await cau tra loi user trong luc harness turn dang chay, roi tra tool result binh thuong de agent loop tiep tuc.
- Them `web_fetch` stdlib HTTP fetch cho `http`/`https`, extract text tu HTML/plain/markdown va tra status/final URL/content type/bytes/truncation.
- Them `web_search` voi provider registry, Tavily/Brave adapters, env credential checks (`TAVILY_API_KEY`, `BRAVE_SEARCH_API_KEY`) va selector `AGENT_SMITH_WEB_SEARCH_PROVIDER`.
- Them `skills` tool de list/read(create load full content)/create/update/delete skill resources qua `ResourceStore`; `list/read` co the dung `ResourceResolver` de thay resolved catalog.
- Them [`src/tools/README.md`](../src/tools/README.md) ghi ro factory, registry assembly, resource behavior va web search config.

### Added - Agent tools tests

- Them unit/integration tests cho base tools, ask-user pause/resume trong agent loop, web fetch/search provider behavior, va skills resource CRUD/read-only handling.

### Changed - Harden unified AI layer v1

- Tach model catalog khoi code sang [`models.catalog.json`](../src/agent_smith/ai/models.catalog.json), giu lookup cu `get_model` / `get_models` / `get_providers` va them API registry model (`register_model`, `register_models`, `clear_models`, `load_models_from_file`, `make_litellm_model`).
- Mo rong `Model` va `StreamOptions` voi metadata / passthrough options (`headers`, `providerOptions`, `compat`, `thinkingLevelMap`, `env`, `maxRetryDelayMs`) de provider/model moi it can sua runtime hon.
- Harden LiteLLM adapter: forward provider options, ho tro ad-hoc model, map reasoning theo `thinkingLevelMap`, doc cache usage, set `responseId` / `responseModel`, sua tool-call index va tranh emit `thinking_end` hai lan.
- Cai thien provider registry: luu `source_id`, unregister dung source, va validate mismatch `model.api` voi provider api.
- Uu tien Google Vertex service-account JSON khi co `GOOGLE_APPLICATION_CREDENTIALS`; Gemini API key chi dung khi khong co Vertex config hoac caller truyen `api_key` ro rang.
- Cap nhat `.env.example` de ghi ro hai mode Google auth: `GEMINI_API_KEY` hoac `GOOGLE_APPLICATION_CREDENTIALS` + project/location.

### Added - AI layer tests

- Them unit tests cho catalog/registry va LiteLLM adapter mock khong can network, gom passthrough options, ad-hoc model, Google Vertex auth precedence, tool-call ordering va thinking stream.

### Added - Agent loop v1

- Port low-level `agent-loop` cua pi sang package moi [`agent_smith.agent`](../src/agent_smith/agent/), giu runtime loop stateless/persistence-free va chua dua DB/session/harness vao v1.
- Them public API `agent_loop`, `agent_loop_continue`, `run_agent_loop`, `run_agent_loop_continue` voi `AgentEventStream` ho tro `async for` va `await .result()`.
- Them agent-level types (`AgentContext`, `AgentLoopConfig`, `AgentTool`, `AgentToolResult`, hook contexts/events) va tool execution sequential/parallel.
- Tach implementation agent loop thanh package nho hon: runner, streaming, tools, utils de de doc va de bao tri.
- Them validate tool arguments bang JSON Schema qua dependency `jsonschema`; validation/tool errors duoc encode thanh error tool result thay vi lam crash loop.
- Them unit tests cho event lifecycle, continue validation, multi-turn tool calls, parallel ordering, blocked/missing/invalid tools va `after_tool_call` override.

### Added - Harness resource/runtime plane

- Them `agent_smith.resources` lam catalog/config layer tach khoi harness runtime: `ResourceStore`, `ResourceResolver`, `MemoryResourceStore`, `FilesystemResourceStore`, `PostgresResourceStore`, va cac kind `skill`, `prompt_template`, `agent_definition`, `mcp_server_config`.
- Them `agent_smith.runtime` de compile `AgentDefinition` thanh `AgentHarnessOptions` qua `AgentFactory`, cung `ToolRegistry` de resolve concrete `AgentTool` objects.
- Them Postgres resource catalog generic voi migration `002_resource_catalog`: bang `resources` va `resource_versions`, versioned JSONB content, soft delete, disabled resources, va scope-level uniqueness.
- Giu `AgentHarness.resources` la resolved snapshot; harness/session khong import resource DB models va khong quan ly resource lifecycle.
- Them unit tests cho memory/filesystem/Postgres resource stores, resolver priority/mapping, va agent factory validation.

### Verified locally

```text
.venv/bin/python -m ruff check src tests
.venv/bin/python -m pytest tests/test_resources_runtime.py tests/test_agent_harness.py -q
.venv/bin/python -m pytest -q                     # 1 live Google provider test failed; local harness/resource tests passed
poetry run ruff check src tests                   # pass
poetry run pytest tests/test_base_tools.py        # 18 passed
poetry run pytest                                 # 1 live Google provider test failed; tools/resource tests passed
poetry run pytest                                 # 79 passed, 2 skipped
```

## [0.1.0] - 2026-06-18

Milestone dau tien: Python project base, unified AI layer, va core Postgres tables theo plan *Agent Smith - Python Base + Unified AI Layer + Core Tables*.

### Added - Project base

- [`pyproject.toml`](../pyproject.toml) — Poetry project, Python `^3.11`, package `agent-smith` tu `src/`.
- [`poetry.lock`](../poetry.lock) — lock dependencies.
- [`.venv/`](../.venv) — virtualenv trong project (`poetry config virtualenvs.in-project true`).
- [`.env.example`](../.env.example) — mau bien moi truong (`OPENAI_API_KEY`, `DATABASE_URL`, ...).
- [`README.md`](../README.md) — huong dan setup, demo, test.
- [`.gitignore`](../.gitignore) — bo qua `.venv`, `.env`, cache, ...

### Added - Unified AI layer (`src/agent_smith/ai/`)

Port hop dong tu `packages/ai` cua pi, san sang cho agent loop / harness phia tren:

| Module | Noi dung |
|--------|----------|
| `types.py` | `Context`, `Message`, content blocks (`TextContent`, `ThinkingContent`, `ImageContent`, `ToolCall`), `Usage`, `StopReason`, `Model`, `StreamOptions`, event types |
| `events.py` | `AssistantMessageEventStream` — `async for` + `await .result()` |
| `registry.py` | API provider registry theo `Model.api` |
| `api.py` | `stream`, `complete`, `stream_simple`, `complete_simple` |
| `env_keys.py` | Resolve API key theo provider tu environment |
| `models.py` | Catalog model + `get_model` / `get_models` / `get_providers` |
| `providers/litellm_provider.py` | Transport da-provider qua LiteLLM (`litellm.acompletion` stream) |
| `providers/faux.py` | Provider offline deterministic (text / thinking / tool_call) cho test & CI |

**Kien truc transport:**

```text
stream/complete API
  -> registry (api = "litellm" | "faux")
  -> litellm adapter (OpenAI, Anthropic, Gemini, ...) HOAC faux offline
  -> AssistantMessageEventStream
```

- `bootstrap_providers()` dang ky ca litellm va faux; litellm duoc lazy-import de faux test khong can load litellm.
- Event protocol: `start`, `text_*`, `thinking_*`, `toolcall_*`, `done`, `error` (tuong thich pi).

**Model catalog ban dau:** `faux/faux-1`, `openai/gpt-4o-mini`, `openai/gpt-4o`, `anthropic/claude-3-5-sonnet-20241022`, `google/gemini-2.5-flash`.

### Added - Examples & tests

- [`examples/demo_ai.py`](../examples/demo_ai.py) — demo faux offline + OpenAI live (`--provider faux|openai|all`).
- [`tests/test_ai_faux.py`](../tests/test_ai_faux.py) — 3 test: text stream events, thinking + tool call, empty queue error.

### Added - Database control plane (`src/agent_smith/db/`)

Theo [05-identity-auth-policy.md](agent-smith-idea/05-identity-auth-policy.md) va [harness-learning.md](harness-learning.md): moi state bam `principal_id`, session la append-only event tree.

| Bang | Muc dich |
|------|----------|
| `principals` | Canonical principal (human, service_account, agent, subagent, system_job) |
| `external_identities` | Map principal ↔ external IdP (`provider` + `subject`, unique) |
| `local_credentials` | Local password tam (MVP auth provider) |
| `sessions` | Session metadata + `current_leaf_id` |
| `session_entries` | Event tree append-only (`type` + `payload` jsonb, `parent_id`) |

- [`db/base.py`](../src/agent_smith/db/base.py) — async SQLAlchemy engine + session factory.
- [`config.py`](../src/agent_smith/config.py) — `pydantic-settings` doc `.env`.

### Added - Migrations & infra

- [`docker-compose.yml`](../docker-compose.yml) — Postgres 16 (`smith` / `smith` / `agent_smith`).
- [`alembic.ini`](../alembic.ini) + [`migrations/`](../migrations/) — Alembic async.
- [`migrations/versions/001_initial_core.py`](../migrations/versions/001_initial_core.py) — migration tao 5 bang core.

### Dependencies

- Runtime: `litellm`, `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`.
- Dev: `pytest`, `pytest-asyncio`, `ruff`.
- **Ghi chu:** `litellm` ghim `1.44.28` — cac ban moi hon co the loi cai dat tren Windows (duong dan dai trong package proxy).

### Verified locally

```text
poetry run pytest -v                              # 3 passed
poetry run python examples/demo_ai.py --provider faux
poetry run alembic heads                          # 001_initial_core (head)
```

Neu terminal chua co `poetry` trong PATH, dung truc tiep `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe examples\demo_ai.py --provider faux
```

### Not in this release (de sau)

Cac bang / module chua implement — se them khi can:

- `identity_links`, `capability_registry`, audit log, memory, `policy_decisions`, `approval_authorities`, `tasks`
- Agent loop, harness, auth API, MCP providers
