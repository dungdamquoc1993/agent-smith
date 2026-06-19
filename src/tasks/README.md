# tasks

Background / sub-agent task runtime. Spawn work, track lifecycle, stream output, read results.

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

## Extending

**Runtime & output** — implement the protocols in `types.py`:

- `TaskRuntime` → e.g. `sql_task_runtime.py`, `fs_task_runtime.py` (persist `TaskRecord` to DB/disk)
- `TaskOutputStore` → e.g. `file_task_output_store.py` (log to files; `TaskRecord.output_path`)

Current defaults are in-memory only.

**Runners** — add one module per `TaskKind` under `runners/` (e.g. `shell.py`, `remote_agent.py`), then expose via tools/factory.
