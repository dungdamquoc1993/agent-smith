# Agent Task Runtime Phase 3

Phase 3 them sub-agent spawning cho Agent Smith, nhung khong nen coi day chi
la mot tool. Claude Code reference cho thay `AgentTool`, background shell, task
output, va task stop deu dua tren mot lop task runtime chung.

Muc tieu cua document nay la lam roadmap de implement tung phan co kiem soat.

## Why This Phase Is Wider Than Agent Tool

`agent` tool can spawn sub-agents. Neu chi implement tool sync truc tiep thi de,
nhung se bi ket ngay khi can:

- chay sub-agent background;
- lay output sau bang `task_output`;
- stop task dang chay;
- sau nay dung chung cho shell/background command;
- bao trang thai task ve host/UI/agent cha;
- gioi han recursion, timeout, quota, va cancel.

Vi vay Phase 3 nen tach thanh:

```text
task runtime core
  -> agent task runner
  -> agent/task tools
  -> config/resource CRUD sau cung
```

Claude Code references dang dung duoc:

- `docs/khowledge_references/claude-code/Task.ts`
- `docs/khowledge_references/claude-code/tasks/LocalAgentTask/LocalAgentTask.tsx`
- `docs/khowledge_references/claude-code/tasks/LocalShellTask/LocalShellTask.tsx`
- `docs/khowledge_references/claude-code/tools/AgentTool/AgentTool.tsx`
- `docs/khowledge_references/claude-code/tools/TaskOutputTool/TaskOutputTool.tsx`
- `docs/khowledge_references/claude-code/utils/task/TaskOutput.ts`

## Naming

Use snake_case cho Smith tools:

```text
agent
task_output
task_stop
```

Khong dung `Task` nhu Claude Code vi trong Smith da co `todo_write` cho planning
stateless. `task_*` o phase nay la runtime task, khong phai todo/task manager
cua user.

## Folder Structure

Khong nen them `src/services` cho phase nay. `services` qua rong va de bien
thanh folder gom moi thu. Nen them package dung domain ro rang:

```text
src/tasks/
  __init__.py
  types.py          # TaskRecord, TaskStatus, TaskKind, TaskResult, snapshots
  errors.py         # TaskRuntimeError, UnknownTaskError, TaskAlreadyFinished
  output.py         # TaskOutputStore protocol + memory/file output helpers
  manager.py        # TaskRuntime / TaskManager lifecycle
  memory.py         # MemoryTaskStore v1 neu can tach khoi manager
  runners/
    __init__.py
    agent.py        # AgentTaskRunner dung AgentFactory + harness session

src/tools/
  agent.py          # create_agent_tool(...)
  task_output.py    # create_task_output_tool(...)
  task_stop.py      # create_task_stop_tool(...)
  utils.py          # optional-register tools khi caller truyen runtime/factory

tests/
  test_task_runtime.py
  test_agent_task_tools.py
```

Neu sau nay co persistence/queue worker:

```text
src/tasks/postgres.py      # persistent metadata/output pointers
src/tasks/workers.py       # process/queue worker boundary
src/tasks/filesystem.py    # file-backed output store
```

`src/services/` chi nen dung sau nay cho integration side effects nhu notifier,
approval service, scheduler adapter, remote runner adapter. Core task lifecycle
nen nam trong `src/tasks`.

## Phase 3a - Task Runtime Core

### Goal

Tao runtime in-memory, process-local, du de spawn async jobs va query/stop/wait.
Task records van khong persist DB; session provenance cua sub-agent duoc them
sau bang migration `003_session_provenance`.

### Public Concepts

```python
TaskKind = Literal["agent", "shell", "remote_agent"]
TaskStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
```

`TaskRecord` nen gom:

```text
id
kind
status
description
created_at
started_at
ended_at
output_path | None
output_bytes
result | None
error | None
metadata
```

`TaskRuntime` API du kien:

```python
class TaskRuntime:
    async def spawn(
        self,
        *,
        kind: TaskKind,
        description: str,
        run: Callable[[TaskContext], Awaitable[TaskResult]],
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord: ...

    async def get(self, task_id: str) -> TaskRecord: ...
    async def list(self) -> list[TaskRecord]: ...
    async def wait(self, task_id: str, timeout_seconds: float | None = None) -> TaskRecord: ...
    async def stop(self, task_id: str, reason: str | None = None) -> TaskRecord: ...
    async def read_output(self, task_id: str, *, max_bytes: int | None = None) -> TaskOutputSnapshot: ...
```

`TaskContext` nen co:

```text
task_id
abort_signal / abort event
append_output(text)
set_result_metadata(...)
```

### Output Store V1

V1 co the in-memory truoc:

```text
MemoryTaskOutputStore
  append(task_id, text)
  read(task_id, max_bytes=None)
  clear(task_id)
```

File-backed output store de sau, nhung interface nen san sang vi Claude Code
uu tien output file cho background task.

### Scope Guard

Phase 3a khong lam:

- agent spawning;
- shell command;
- Postgres persistence;
- remote runner;
- UI notification queue;
- task scheduling/retry.

### Tests

- spawn task thanh cong -> status `completed`, result duoc luu.
- spawn task raise exception -> status `failed`, error duoc luu.
- stop running task -> abort signal duoc set, status `cancelled`.
- wait timeout -> tra loi timeout ro rang hoac current snapshot.
- output append/read/tail theo dung order.
- unknown task -> `UnknownTaskError`.

## Phase 3b - Agent Task Runner

### Goal

Dung `AgentFactory` hien co de tao harness con va chay sub-agent.

### New Module

```text
src/tasks/runners/agent.py
```

Public class du kien:

```python
class AgentTaskRunner:
    def __init__(
        self,
        *,
        agent_factory: AgentFactory,
        session_factory: Callable[[AgentChildSessionRequest], AgentHarnessSession],
        max_depth: int = 3,
    ) -> None: ...

    async def run(
        self,
        *,
        task_context: TaskContext,
        agent_name: str,
        prompt: str,
        parent_metadata: dict[str, Any] | None = None,
    ) -> AgentTaskResult: ...
```

### Session Strategy

V1 nen dung session con rieng:

```text
parent harness/session
  -> agent tool call
    -> child AgentHarnessSession
    -> child AgentHarness
```

Child session co the dung memory hoac Postgres backend. Neu persist vao
Postgres, `SessionMetadata` dung `kind="sub_agent"`, `parent_session_id`,
`agent_name`, `origin_task_id`, va `provenance` de trace parent session/task.

### Abort Rules

- Sync sub-agent: parent abort nen abort child.
- Async sub-agent: khong bi parent abort tu dong; chi stop qua `task_stop`.
- Process shutdown: runtime cleanup abort all running tasks.

### Recursion Guard

Can co metadata/depth:

```text
parent_task_id
parent_agent_name
agent_depth
```

Default `max_depth=3`. Khi vuot depth, tool tra error result thay vi spawn.

### Result Shape

`AgentTaskResult`:

```text
agent_name
status
final_text
message_id | None
usage | None
turns
```

V1 chi can final assistant text. Full transcript nam trong child session, khong
can dua het vao tool result.

### Tests

- runner tao harness tu `AgentFactory`.
- fake model final text -> result final_text dung.
- missing agent definition -> failed/error ro.
- child tools_allow/tools_deny duoc ap dung qua factory.
- sync abort propagate vao child.
- depth guard chan recursion.

## Phase 3c - Agent Tool And Task Tools

### `agent` Tool

Factory:

```python
create_agent_tool(
    *,
    task_runtime: TaskRuntime,
    agent_runner: AgentTaskRunner,
    default_mode: Literal["sync", "async"] = "sync",
    sync_timeout_seconds: float | None = None,
) -> AgentTool
```

Input:

```text
agent_name        # AgentDefinition.name
description       # short task label
prompt            # task prompt
mode              # sync | async
timeout_seconds   # optional sync wait cap
```

Sync output:

```json
{
  "status": "completed",
  "agentName": "reviewer",
  "result": "...",
  "taskId": "a..."
}
```

Async output:

```json
{
  "status": "launched",
  "agentName": "reviewer",
  "taskId": "a...",
  "description": "...",
  "outputPath": null
}
```

V1 co the de `outputPath=null` neu output store dang in-memory. Khi co file
store thi tra path.

### `task_output` Tool

Factory:

```python
create_task_output_tool(task_runtime: TaskRuntime) -> AgentTool
```

Input:

```text
task_id
block = true
timeout_seconds = 30
max_bytes = 100000
```

Output:

```text
retrieval_status = success | timeout | not_ready
task = { id, kind, status, description, output, result, error }
```

### `task_stop` Tool

Factory:

```python
create_task_stop_tool(task_runtime: TaskRuntime) -> AgentTool
```

Input:

```text
task_id
reason | None
```

Output:

```text
status = cancelled | completed | failed | not_running
task = snapshot
```

### Registry Helper

`create_base_tool_registry` khong nen tu dong add agent tools neu caller khong
truyen runtime/factory. Them optional params:

```python
create_base_tool_registry(
    ...,
    task_runtime: TaskRuntime | None = None,
    agent_runner: AgentTaskRunner | None = None,
    agent_parent_metadata: Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None = None,
)
```

Neu co ca hai:

```text
add agent
add task_output
add task_stop
```

Neu chi co `task_runtime`:

```text
add task_output
add task_stop
```

### Tests

- `agent` sync chay den final response.
- `agent` async return task id ngay.
- `task_output block=false` tra not_ready khi task dang running.
- `task_output block=true` wait den completed.
- `task_stop` abort running async task.
- registry optional add dung tool names.

## Phase 3d - Agent Config CRUD

Chi lam sau khi Phase 3c green.

Tool co the la `agents` hoac `agent_configs`, nhung nen tach khoi `agent` spawn
tool. Ly do: spawn la runtime action, config CRUD la resource management.

Factory:

```python
create_agents_tool(store: ResourceStore, resolver: ResourceResolver | None = None)
```

Actions:

```text
list
read
create
update
delete
```

Resource kind:

```text
agent_definition
```

Noi dung map vao `AgentDefinition`:

```text
name
description
systemPrompt
whenToUse
toolsAllow
toolsDeny
skills
promptTemplates
mcpServers
model
thinkingLevel
maxTurns
permissionMode
```

Khong gop vao Phase 3c de tranh vua lam lifecycle runtime vua lam config UX.

## Phase 3e - Shell/Command Background Runtime

Day la future phase, khong nen lam ngay.

Khi lam shell tool, no nen dung chung:

```text
TaskRuntime
TaskOutputStore
task_output
task_stop
```

Luc do them:

```text
src/tasks/runners/shell.py
src/tools/shell.py
```

Nhung production enterprise co the khong expose raw shell mac dinh.

## Implementation Order

1. Them `src/tasks` voi in-memory runtime va tests.
2. Them `AgentTaskRunner` sync-only, fake model tests.
3. Them `agent` tool sync mode.
4. Them async mode trong `agent` tool.
5. Them `task_output` va `task_stop`.
6. Update `create_base_tool_registry` optional params.
7. Update `src/tools/README.md` va `docs/CHANGELOG.md`.
8. Rerun:

```text
poetry run ruff check src tests
poetry run pytest tests/test_task_runtime.py tests/test_agent_task_tools.py
poetry run pytest
```

## Design Decisions For V1

- Task runtime v1 la in-memory/process-local.
- Co migration `003_session_provenance` cho session-level provenance cua sub-agent.
- Background tasks mat neu process restart; day la chap nhan duoc cho v1.
- Agent child session rieng, khong mutate parent session truc tiep.
- Async sub-agent khong bi parent abort tu dong.
- Sync sub-agent bi parent abort.
- `task_output` co the doc in-memory output truoc, file output sau.
- Agent config CRUD de Phase 3d.
- MCP dynamic tools de phase rieng.
- Shell/background command de phase rieng.

## Open Questions

- Tool name cuoi cung nen la `agent` hay `spawn_agent`?
- Co can expose `task_list` ngay trong Phase 3c khong, hay host/UI query runtime
  truc tiep la du?
- Child session id nen duoc dat theo `task_id` hay UUID rieng?
- Output store default nen in-memory hay file-backed ngay tu dau?
- Async task notification nen di qua harness event, pending message queue, hay
  host callback rieng?
- Co nen deny recursive `agent` tool mac dinh, roi opt-in theo agent definition?
