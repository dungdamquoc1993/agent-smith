# Agent Smith

Enterprise agent runtime — Python core với unified AI layer, agent loop/harness nhiều turn, và Postgres control plane.

## Kiến trúc

```
Caller (app / service)
       │
       ▼
  app/services/         ← transport-neutral use cases
       │
       ▼
  core/runtime/         ← AgentDefinition → AgentHarnessOptions → AgentHarness
       │                    (ResourceResolver + ToolRegistry)
       ▼
  core/agent/harness/   ← stateful: session, hooks, compaction, queues
       │
       ▼
  core/agent/agent_loop/← turn loop: stream → tool → lặp
       │
       ▼
  core/llm/             ← stream / complete LLM abstraction
       │
       ▼
  infra/                ← Postgres, LiteLLM, MCP SDK, config
```

**Catalog & persistence** tách riêng:

```
core/resources/         ← skill, prompt_template, agent_definition, mcp_server_config
       │
       ▼
  core/agent/harness/session/ + infra/persistence/
                         ← session tree (memory hoặc Postgres)
```

| Package | Vai trò | Chi tiết |
|---------|---------|----------|
| `agent_smith.core.llm` | Gọi LLM thống nhất: catalog model, registry provider, stream events | Entry: `stream` / `complete` |
| `agent_smith.core.agent` | Runtime agent nhiều turn: loop engine + harness shell | Types, events, validation |
| `agent_smith.core.resources` | Catalog definitions và resolver contract | Resolve → snapshot cho harness |
| `agent_smith.core.runtime` | Assembly: blueprint → harness instance | `AgentFactory` |
| `agent_smith.app` | Use-case services transport-neutral | session/resource/task/agent-run orchestration |
| `agent_smith.infra` | Concrete adapters | Postgres, LiteLLM, MCP SDK, settings |
| `agent_smith.transports` | API/message adapters | HTTP/SSE adapter now, message adapters later |

**Khi nào dùng gì?**

- Chỉ gọi model (test, script) → `agent_smith.core.llm`
- Embed loop tối thiểu, tự quản context → `agent_smith.core.agent.agent_loop`
- Production multi-turn, persist session → `agent_smith.core.agent.harness`
- Load skill/template/agent config từ catalog → `agent_smith.core.resources` → `agent_smith.core.runtime`
- Expose HTTP/SSE hoặc queue consumer → `agent_smith.app` service trước, transport adapter sau

## Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/docs/#installation)
- Docker (cho Postgres)

## Setup

```bash
poetry install
cp .env.example .env
# Chỉnh .env: OPENAI_API_KEY, DATABASE_URL, ...

docker compose up -d
poetry run alembic upgrade head
```

## Demo unified AI layer

```bash
# OpenAI (cần OPENAI_API_KEY trong .env)
poetry run python examples/demo_ai.py --provider openai

# Google Gemini qua Vertex (service account trong .gcp/ hoặc GEMINI_API_KEY)
poetry run python examples/demo_ai.py --provider google

# Cả hai
poetry run python examples/demo_ai.py --provider all
```

Google hỗ trợ hai mode auth: `GEMINI_API_KEY` hoặc `GOOGLE_APPLICATION_CREDENTIALS` + project/location — xem `.env.example`.

## Tests

```bash
poetry run pytest
poetry run ruff check src tests
```

## Cấu trúc repo

```
src/agent_smith/
├── core/               # pure runtime contracts and orchestration
├── app/                # transport-neutral use-case services
├── infra/              # DB/provider/MCP concrete adapters
├── transports/         # HTTP/SSE now, messaging contracts later
└── workers/            # worker skeleton/entrypoints

clients/web/            # future React/Vite test client
tests/                  # unit tests
docs/                   # changelog, design notes
migrations/             # Alembic migrations
```

Lịch sử thay đổi implementation: [`docs/CHANGELOG.md`](docs/CHANGELOG.md).
