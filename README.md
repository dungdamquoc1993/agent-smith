# Agent Smith

Enterprise agent runtime — Python core với unified AI layer, agent loop/harness nhiều turn, và Postgres control plane.

## Kiến trúc

```
Caller (app / service)
       │
       ▼
  runtime/              ← AgentDefinition → AgentHarnessOptions → AgentHarness
       │                    (ResourceResolver + ToolRegistry)
       ▼
  harness/              ← stateful: session, hooks, compaction, queues
       │
       ▼
  agent_loop/           ← turn loop: stream → tool → lặp
       │
       ▼
  ai/                   ← stream / complete LLM (LiteLLM)
```

**Catalog & persistence** tách riêng:

```
resources/              ← skill, prompt_template, agent_definition, mcp_server_config
       │
       ▼
  harness/session/      ← session tree (memory hoặc Postgres)
```

| Package | Vai trò | Chi tiết |
|---------|---------|----------|
| [`ai`](src/agent_smith/ai/README.md) | Gọi LLM thống nhất: catalog model, registry provider, stream events | Entry: `stream` / `complete` |
| [`agent`](src/agent_smith/agent/README.md) | Runtime agent nhiều turn: loop engine + harness shell | Types, events, validation |
| [`agent_loop`](src/agent_smith/agent/agent_loop/README.md) | Orchestration **stateless**: stream → tool → lặp | `run_agent_loop`, hooks qua config |
| [`harness`](src/agent_smith/agent/harness/README.md) | Orchestration **stateful**: session, compact, steer/follow-up | `AgentHarness.prompt()` |
| [`session`](src/agent_smith/agent/harness/session/README.md) | Session tree append-only, fork/branch | Memory / Postgres backend |
| [`resources`](src/agent_smith/resources/README.md) | Catalog definitions (memory, Postgres) | Resolve → snapshot cho harness |
| [`runtime`](src/agent_smith/runtime/README.md) | Assembly: blueprint → harness instance | `AgentFactory` |

**Khi nào dùng gì?**

- Chỉ gọi model (test, script) → `ai`
- Embed loop tối thiểu, tự quản context → `agent_loop`
- Production multi-turn, persist session → `harness` (+ `session`)
- Load skill/template/agent config từ catalog → `resources` → `runtime`

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
├── ai/                 # LLM layer
├── agent/
│   ├── agent_loop/     # stateless turn engine
│   └── harness/        # stateful shell + session/
├── resources/          # catalog stores & resolver
├── runtime/            # AgentFactory assembly
└── db/                 # SQLAlchemy models, migrations ở migrations/

examples/               # demo scripts
tests/                  # unit tests
docs/                   # changelog, design notes
```

Lịch sử thay đổi implementation: [`docs/CHANGELOG.md`](docs/CHANGELOG.md).
