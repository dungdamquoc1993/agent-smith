# Phase 3 Agent Task Runtime Overview

This document is a fast reading map for the Phase 3 implementation. It only
references implementation files, not tests.

## What Phase 3 Adds

Phase 3 adds a shared runtime layer for long-running work, then builds sub-agent
execution and tools on top of it:

```text
Task runtime core
  -> Agent task runner
  -> Runtime task tools
  -> Agent config CRUD tool
```

The split matters because `agent` spawning, future shell/background commands,
task output polling, and task stopping should share the same lifecycle model.

## Core Runtime

Read these first:

- [`src/tasks/types.py`](src/tasks/types.py) defines the public contracts:
  `TaskRuntime`, `TaskRecord`, `TaskContext`, `TaskOutputSnapshot`, task kinds,
  statuses, and result typing.
- [`src/tasks/memory.py`](src/tasks/memory.py) implements `MemoryTaskRuntime`.
  It owns task records, `asyncio.Task` handles, abort events, status transitions,
  wait/stop/read APIs, and snapshot isolation.
- [`src/tasks/output.py`](src/tasks/output.py) implements `MemoryTaskOutputStore`
  and the `TaskOutputStore` protocol.
- [`src/tasks/errors.py`](src/tasks/errors.py) defines runtime errors:
  `UnknownTaskError`, `TaskAlreadyFinishedError`, and `TaskTimeoutError`.
- [`src/tasks/__init__.py`](src/tasks/__init__.py) exports the public task API.

Main lifecycle:

```text
spawn(...)
  -> creates TaskRecord(status="running")
  -> creates TaskContext
  -> starts asyncio task

run returns
  -> completed + result

run raises
  -> failed + error

stop(...)
  -> sets abort event
  -> cancels asyncio task
  -> cancelled + reason
```

Important current limits:

- Runtime is in-memory and process-local.
- `outputPath` is always `None` for now.
- There is no queue worker, DB persistence, retry, or shell runner yet.

## Agent Runner

Read next:

- [`src/tasks/runners/agent.py`](src/tasks/runners/agent.py) implements
  `AgentTaskRunner`, `AgentTaskResult`, and `AgentTaskRunnerError`.
- [`src/tasks/runners/__init__.py`](src/tasks/runners/__init__.py) exports runner
  symbols.

`AgentTaskRunner` is not a tool. It is the callable runner used by the tool
layer. It receives a `TaskContext`, creates a child session through an injected
`session_factory`, builds a child harness through `AgentFactory`, and calls
`harness.prompt(prompt)`.

Key behavior:

- Child agent runs in a separate `AgentHarnessSession`.
- `parent_metadata.agentDepth` is used as a recursion guard.
- Runner writes useful progress to `TaskContext.append_output(...)`.
- Runner writes `agentName`, `agentDepth`, `sessionId`, `stopReason`, and `turns`
  into task result metadata.
- If the task abort signal is set, it aborts the child harness and raises
  cancellation so `MemoryTaskRuntime` records `cancelled`.

## Runtime Tools

Read these after the runner:

- [`src/tools/agent.py`](src/tools/agent.py) defines the `agent` tool.
- [`src/tools/task_output.py`](src/tools/task_output.py) defines `task_output`.
- [`src/tools/task_stop.py`](src/tools/task_stop.py) defines `task_stop`.
- [`src/tools/_task_serialization.py`](src/tools/_task_serialization.py)
  normalizes task records, Pydantic models, datetimes, output snapshots, and
  agent task results into JSON-friendly tool details.

Tool responsibilities:

```text
agent
  sync  -> spawn task, wait to terminal state, return result/output
  async -> spawn task, return task id immediately

task_output
  block=false -> return current snapshot
  block=true  -> wait for terminal state or timeout

task_stop
  running task  -> stop and return cancelled snapshot
  terminal task -> return existing snapshot with stopped=false
```

The `agent` runtime tool is intentionally different from the `agents` config
tool below:

```text
agent   = run/spawn a sub-agent task
agents  = manage agent definition resources
```

## Agent Config Tool

Read:

- [`src/tools/agents.py`](src/tools/agents.py) defines the `agents` tool.

`agents` manages `agent_definition` resources through an injected
`ResourceStore`. It follows the same pattern as the `skills` tool:

```text
list   -> list available agent definitions
read   -> load full AgentDefinition content
create -> create ResourceCreate(kind="agent_definition")
update -> merge supplied fields into existing content
delete -> soft delete through ResourceStore
```

Important behavior:

- `list/read` can use `ResourceResolver` so the caller sees the resolved catalog.
- Writes always go to the injected store.
- Content is validated with `AgentDefinition`.
- The tool does not validate referenced tool names, skills, models, or MCP
  servers. That stays in `AgentFactory` when the agent is compiled/run.
- Rename is not supported in v1; `name` is the resource identifier.

## Registry Assembly

Read:

- [`src/tools/utils.py`](src/tools/utils.py) wires optional tool registration in
  `create_base_tool_registry(...)`.
- [`src/tools/__init__.py`](src/tools/__init__.py) exports all public factories
  and tool constants.

Current optional registration rules:

```text
base registry only
  -> sleep, todo_write, ask_user_question, web_fetch, web_search

skills_store provided
  -> add skills

agents_store provided
  -> add agents

task_runtime provided
  -> add task_output, task_stop

task_runtime + agent_runner provided
  -> add agent, task_output, task_stop
```

## Existing Resource/Runtime Dependencies

These files were not introduced by Phase 3, but they are important for reading
the flow:

- [`src/runtime/agent_factory.py`](src/runtime/agent_factory.py) compiles an
  `AgentDefinition` into an `AgentHarness`.
- [`src/runtime/tool_registry.py`](src/runtime/tool_registry.py) resolves active
  tools for an agent.
- [`src/resources/types.py`](src/resources/types.py) defines `AgentDefinition`
  and resource record models.
- [`src/resources/resolver.py`](src/resources/resolver.py) maps resource records
  into runtime snapshots and resolves agent definitions.
- [`src/agent/harness/agent_harness.py`](src/agent/harness/agent_harness.py)
  owns prompt execution, abort behavior, session writes, and event emission.

## Recommended Reading Order

1. [`src/tasks/types.py`](src/tasks/types.py)
2. [`src/tasks/memory.py`](src/tasks/memory.py)
3. [`src/tasks/runners/agent.py`](src/tasks/runners/agent.py)
4. [`src/tools/agent.py`](src/tools/agent.py)
5. [`src/tools/task_output.py`](src/tools/task_output.py)
6. [`src/tools/task_stop.py`](src/tools/task_stop.py)
7. [`src/tools/agents.py`](src/tools/agents.py)
8. [`src/tools/utils.py`](src/tools/utils.py)

## Verification Snapshot

Latest local verification after Phase 3:

```text
poetry run ruff check src tests
poetry run pytest
```

Result:

```text
All checks passed
79 passed, 2 skipped
```
