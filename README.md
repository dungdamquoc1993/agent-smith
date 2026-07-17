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
  core/agent/harness/session/ + infra/storage/postgres/
                         ← session port + Postgres adapter
```

| Package | Vai trò | Chi tiết |
|---------|---------|----------|
| `agent_smith.core.llm` | Gọi LLM thống nhất: catalog model, registry provider, stream events | Entry: `stream` / `complete` |
| `agent_smith.core.agent` | Runtime agent nhiều turn: loop engine + harness shell | Types, events, validation |
| `agent_smith.core.resources` | Catalog definitions và resolver contract | Resolve → snapshot cho harness |
| `agent_smith.core.runtime` | Assembly: blueprint → harness instance | `AgentFactory` |
| `agent_smith.app` | Use-case services transport-neutral | session/resource/task/agent-run orchestration |
| `agent_smith.infra` | Concrete adapters | Postgres, LiteLLM, MCP SDK, settings |
| `agent_smith.transports` | HTTP adapters | FastAPI + SSE (`/api/agent/invoke/stream`, …) |
| `agent_smith.workers` | Scale-out boundary | Placeholder; agent runs still execute in-process on the HTTP request today |

**Khi nào dùng gì?**

- Chỉ gọi model (test, script) → `agent_smith.core.llm`
- Embed loop tối thiểu, tự quản context → `agent_smith.core.agent.agent_loop`
- Production multi-turn, persist session → `agent_smith.core.agent.harness`
- Load skill/template/agent config từ catalog → `agent_smith.core.resources` → `agent_smith.core.runtime`
- Expose runtime to callers → `agent_smith.app` service + `agent_smith.transports.http` (SSE on the same request)

## Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/docs/#installation)
- Docker (cho Postgres)

## Setup

```bash
poetry install
cp .env.example .env
# Chỉnh .env: OPENROUTER_API_KEY, AGENT_SMITH_POSTGRES_URL, ...

docker compose -f docker/compose.yml up -d
poetry run alembic upgrade head
```

Docker and local dependency files live in
[`docker`](docker/README.md).

Model switcher được tạo từ `src/agent_smith/core/llm/models.catalog.json` và chỉ hiện
model khi `OPENROUTER_API_KEY` đã được cấu hình. Public `modelKey` độc lập với provider;
catalog giữ OpenRouter route nội bộ và API chỉ trả các key user thực sự gọi được.
Model mặc định được cấu hình riêng bằng `AGENT_SMITH_DEFAULT_MODEL`.

## Managed files and image input

Managed files keep binary data in private S3-compatible storage and metadata in
Postgres. Upload through the file API, complete the upload, then send only
`payload.attachments: [{"fileId": "..."}]` to `/api/agent/invoke/stream`.
PNG, JPEG, GIF, and WebP are materialized only for the provider call; session
history stores immutable file references, never binary/base64. Integration
details and error contracts: [Parent App Integration](docs/PARENT_APP_INTEGRATION.md).

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
├── infra/              # concrete adapters; storage split by backend
├── transports/         # HTTP/SSE
└── workers/            # scale-out boundary (placeholder; HTTP runs in-process today)

clients/web/            # future React/Vite test client
tests/                  # unit tests
docs/                   # changelog, design notes
migrations/             # Alembic migrations
```

Lịch sử thay đổi implementation: [`docs/CHANGELOG.md`](docs/CHANGELOG.md).
