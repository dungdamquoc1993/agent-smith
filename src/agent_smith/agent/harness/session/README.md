# Session

Module quản lý **session tree** của harness: lịch sử hội thoại dạng cây append-only, có thể fork/branch.

## Luồng tổng quan

```
Caller / Harness
       │
       ▼
  SessionRepo          ← quản lý nhiều session (create / open / fork)
       │
       ▼
    Session            ← đối tượng dùng hàng ngày (append, đọc branch, build context)
       │
       ▼
  SessionStorage       ← persistence của **một** session
```

## Ba lớp

| Lớp | Vai trò |
|-----|---------|
| **`SessionRepo`** | Factory cho nhiều session: `create`, `open`, `fork`. |
| **`Session`** | Facade bọc một `SessionStorage`. Harness gọi lớp này để ghi/đọc. |
| **`SessionStorage`** | Lưu trữ dữ liệu của **một** session (entries, leaf, metadata). |

## Backend

Hai cặp implementation cùng contract (`SessionRepo` + `SessionStorage`):

- **`MemorySessionRepo` / `MemorySessionStorage`** — in-memory, dùng test/dev.
- **`PostgresSessionRepo` / `PostgresSessionStorage`** — persist Postgres.

Cả hai đều trả về cùng kiểu `Session`; harness không cần biết backend.

## Dữ liệu bên trong một session

- **Tree entries** (`SessionTreeEntry`): message, model change, compaction, label, … nối nhau qua `parent_id`.
- **Leaf** (`current_leaf_id`): đỉnh nhánh hiện tại.
- **`get_branch()`** — đi từ leaf lên root → nhánh đang active.
- **`build_context()`** — project nhánh đó thành `SessionContext` (messages, model, thinking level, tools).

Ghi luôn append-only; đổi nhánh bằng `move_to(entry_id)` (chỉ đổi leaf, không xóa entry).

## Ví dụ luồng

```python
repo = PostgresSessionRepo(session_factory)

session = await repo.create(principal_id="user-1")   # Session mới
await session.append_message(user_msg)
await session.append_message(assistant_msg)

ctx = await session.build_context()                  # Đọc nhánh hiện tại

session2 = await repo.open({"id": session_id})       # Mở lại
fork = await repo.fork(source={"id": session_id})    # Fork nhánh
```
