# Agent Smith Service-Ready Monorepo Refactor

Date: 2026-07-06

## Summary

This refactor moves Agent Smith from several top-level Python packages into a
single `agent_smith` namespace and introduces service-ready boundaries for API,
worker, and future message-bus entrypoints.

The goal is to keep the agent runtime reusable while preventing HTTP server
code, Postgres adapters, model-provider adapters, and worker concerns from
leaking into the core runtime.

## New Layout

```text
src/agent_smith/
  core/
    agent/          # agent loop, harness, session contracts, agent types
    llm/            # model catalog, LLM API contracts, stream events
    permissions/    # permission resolver, rules, host callbacks
    resources/      # resource contracts, memory store, resolver
    runtime/        # AgentFactory and ToolRegistry
    tasks/          # task runtime contracts, memory runtime, agent runner
    tools/          # native/base tool factories

  app/
    container.py    # application composition root
    envelope.py     # transport-neutral command envelope
    events.py       # app-level event envelope
    services/       # session/resource/task/agent-run use cases

  infra/
    config.py       # settings
    db/             # SQLAlchemy base and models
    llm/            # LiteLLM provider adapter
    mcp/            # MCP runtime and SDK transport
    persistence/    # Postgres resource/session adapters

  transports/
    http/           # local HTTP/SSE test adapter
    messaging/      # future Redis/RMQ/Kafka contracts

  workers/          # queue-worker skeleton

clients/web/        # placeholder for the future React/Vite test client
migrations/         # Alembic migrations
```

## Boundary Rules

- `agent_smith.core` should stay transport-neutral and mostly infrastructure-free.
- Postgres-backed stores and repos live under `agent_smith.infra.persistence`.
- SQLAlchemy models and DB setup live under `agent_smith.infra.db`.
- LLM provider implementation lives under `agent_smith.infra.llm`; core keeps
  model and streaming contracts.
- HTTP/SSE code calls `agent_smith.app` services instead of constructing
  `AgentFactory`, stores, DB sessions, or model registries directly.
- Future Redis/RMQ/Kafka consumers should use the same command/event envelope
  types as the HTTP transport.

## Public App Layer

The new app layer exposes use-case services:

- `SessionService`
- `ResourceService`
- `TaskService`
- `AgentRunService`

Transport-neutral envelopes were added:

```text
CommandEnvelope:
  command_id
  correlation_id
  idempotency_key
  principal_id
  session_id
  task_id
  trace_id
  payload

AppEventEnvelope:
  event_id
  event_type
  correlation_id
  principal_id
  session_id
  task_id
  created_at
  payload
```

## Local HTTP Adapter

The local HTTP adapter lives in:

```text
agent_smith.transports.http
```

Run it with:

```bash
poetry run python -m agent_smith.transports.http.main
```

## Packaging And Imports

Poetry now packages only:

```text
agent_smith
```

Old imports such as `from agent import ...`, `from ai import ...`, or
`from db.base import ...` were replaced with namespaced imports such as:

```python
from agent_smith.core.agent import AgentHarness
from agent_smith.core.llm import stream
from agent_smith.infra.db.base import Base
from agent_smith.infra.persistence import PostgresSessionRepo
```

No compatibility re-export layer was kept for the old top-level packages.

## Verification

The refactor was verified with:

```bash
poetry run pytest -q
poetry run ruff check src tests
poetry run python -m compileall -q src tests migrations
```

At the time of the refactor, the full test suite passed with:

```text
110 passed, 6 skipped
```
