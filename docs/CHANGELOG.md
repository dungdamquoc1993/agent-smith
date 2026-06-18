# Changelog

Lich su thay doi implementation cua Agent Smith (code, khong phai design notes).

Design notes van nam trong [agent-smith-idea/](agent-smith-idea/).

---

## [Unreleased]

### Changed - Harden unified AI layer v1

- Tach model catalog khoi code sang [`models.catalog.json`](../src/agent_smith/ai/models.catalog.json), giu lookup cu `get_model` / `get_models` / `get_providers` va them API registry model (`register_model`, `register_models`, `clear_models`, `load_models_from_file`, `make_litellm_model`).
- Mo rong `Model` va `StreamOptions` voi metadata / passthrough options (`headers`, `providerOptions`, `compat`, `thinkingLevelMap`, `env`, `maxRetryDelayMs`) de provider/model moi it can sua runtime hon.
- Harden LiteLLM adapter: forward provider options, ho tro ad-hoc model, map reasoning theo `thinkingLevelMap`, doc cache usage, set `responseId` / `responseModel`, sua tool-call index va tranh emit `thinking_end` hai lan.
- Cai thien provider registry: luu `source_id`, unregister dung source, va validate mismatch `model.api` voi provider api.
- Uu tien Google Vertex service-account JSON khi co `GOOGLE_APPLICATION_CREDENTIALS`; Gemini API key chi dung khi khong co Vertex config hoac caller truyen `api_key` ro rang.
- Cap nhat `.env.example` de ghi ro hai mode Google auth: `GEMINI_API_KEY` hoac `GOOGLE_APPLICATION_CREDENTIALS` + project/location.

### Added - AI layer tests

- Them unit tests cho catalog/registry va LiteLLM adapter mock khong can network, gom passthrough options, ad-hoc model, Google Vertex auth precedence, tool-call ordering va thinking stream.

### Verified locally

```text
poetry run ruff check src tests --output-format=concise
poetry run pytest -q                              # 12 passed
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
