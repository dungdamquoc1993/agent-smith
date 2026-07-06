# tasks

Background / agent task runtime. Spawn work, track lifecycle, stream output, read results.

## Layout

| File | Role |
|------|------|
| `types.py` | Contracts (`TaskRuntime`, `TaskOutputStore`), models (`TaskRecord`, `TaskOutputSnapshot`), `TaskContext` |
| `memory_task_runtime.py` | In-memory `TaskRuntime` — spawn, wait, stop, task state |
| `memory_task_output_store.py` | In-memory `TaskOutputStore` — append/read task log text |
| `runners/` | Per-kind execution logic (`AgentTaskRunner`, …) |
| `errors.py` | Typed runtime errors |

## Flow

```
caller → TaskRuntime.spawn(run=…) → TaskContext → runner
caller → TaskRuntime.get / wait / read_output   (observe from outside)
```

- **Runner** writes via `TaskContext` (`append_output`, `set_result_metadata`); returns final `result`.
- **Runtime** owns lifecycle; does not auto-dispatch by `kind` — tools wire `kind` + runner.
- **AgentTaskRunner** creates child sessions through
  `AgentChildSessionRequest`, so callers can preserve agent-run provenance
  without coupling the runner to Postgres.

## Extending

**Runtime & output** — implement the protocols in `types.py`:

- `TaskRuntime` → e.g. `sql_task_runtime.py`, `fs_task_runtime.py` (persist `TaskRecord` to DB/disk)
- `TaskOutputStore` → e.g. `file_task_output_store.py` (log to files; `TaskRecord.output_path`)

Current defaults are in-memory only.

Persisting task records is separate from persisting agent sessions. An agent run
can use a Postgres-backed child session even while `MemoryTaskRuntime` owns the
task lifecycle.

**Runners** — add one module per `TaskKind` under `runners/` (e.g. `shell.py`, `remote_agent.py`), then expose via tools/factory.
