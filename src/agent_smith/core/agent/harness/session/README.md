# Session

← [Harness](../README.md) · [Agent (overview)](../../README.md)

Module quản lý **một session tree** của harness: lịch sử hội thoại dạng cây append-only,
có thể di chuyển giữa các branch.

## Luồng tổng quan

```
Application            ← quản lý lifecycle: create / open / fork / list
       │
       │ inject một session đã resolve
       ▼
    Harness
       │
       ▼
    Session            ← append, đọc branch, build context
       │
       ▼
  SessionStorage       ← persistence port của đúng một session
```

## Hai lớp

| Lớp | Vai trò |
|-----|---------|
| **`Session`** | Facade bọc một `SessionStorage`. Harness gọi lớp này để ghi/đọc. |
| **`SessionStorage`** | Lưu trữ dữ liệu của **một** session (entries, leaf, metadata). |

`AgentHarnessSession` là contract tối thiểu mà harness nhận. `Session` implement contract đó,
nhưng caller cũng có thể cung cấp implementation khác.

## Persistence và lifecycle

- Core chỉ định nghĩa behavior và persistence port của một session.
- Concrete storage nằm ở infra, hiện tại là `PostgresSessionStorage`.
- Application layer chọn cách create/open/fork/list session qua `SessionCatalog` port.
- `PostgresSessionCatalog` thực hiện lifecycle; riêng `fork` là một transaction atomic.
- In-memory implementation chỉ là test double dưới `tests/helpers`, không phải runtime backend.

Harness không nhận repository và không quản lý tập hợp session.

## Dữ liệu bên trong một session

- **Metadata** (`SessionMetadata`): `kind` (`chat` hoặc `agent_run`),
  `principal_id`, `parent_session_id`, `agent_name`, `origin_task_id`,
  `provenance`.
- **Tree entries** (`SessionTreeEntry`): message, model change, compaction, label, … nối nhau qua
  `parent_id`.
- **Leaf** (`current_leaf_id`): đỉnh nhánh hiện tại.
- **`get_branch()`** — đi từ leaf lên root → nhánh đang active.
- **`build_context()`** — project nhánh đó thành `SessionContext` (messages, model, thinking level,
  tools).

Ghi luôn append-only; đổi nhánh bằng `move_to(entry_id)` (chỉ đổi leaf, không xóa entry).

## Ví dụ application wiring

```python
catalog = PostgresSessionCatalog(session_factory)

session = await catalog.create(principal_id="user-1")
harness = AgentHarness(session=session, model=model)

await session.append_message(user_msg)
await session.append_message(assistant_msg)

ctx = await session.build_context()

session2 = await catalog.open({"id": session_id})
fork = await catalog.fork(source={"id": session_id})
```

Agent-run session dùng cùng contract, chỉ khác metadata:

```python
child = await catalog.create(
    principal_id="user-1",
    kind="agent_run",
    parent_session_id=session_id,
    agent_name="reviewer",
    origin_task_id="task_123",
    provenance={"trigger": "task_tool", "mode": "sync"},
)
```

## Context từ các session khác

Recent conversations là context enrichment read-only, không phải session lifecycle. Application
có thể inject một `RecentConversationProvider` vào harness. Provider query các session khác và chỉ
trả về `RecentConversationSnapshot`; harness vẫn vận hành đúng một current session.
