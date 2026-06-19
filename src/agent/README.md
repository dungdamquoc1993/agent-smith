# Agent

Runtime agent nhiều turn: gọi LLM, chạy tool, giữ session, compact context. Package này nằm giữa **`ai`** (gọi model) và caller (app, CLI, service).

## Vị trí trong stack

```
Caller (app / service)
       │
       ▼
  harness/              ← stateful: session, hooks, compaction, queues
       │
       ▼
  agent_loop/           ← turn loop: stream → tool → lặp
       │
       ▼
  ai/                   ← stream / complete LLM
```

**Đi vào từ đâu?**

| Nhu cầu | Dùng |
|---------|------|
| Production, có session & hooks | [`AgentHarness`](harness/README.md) |
| Embed loop tối thiểu, tự quản context | [`agent_loop`](agent_loop/README.md) |
| Chỉ types / events | `types.py`, `events.py` |

Chi tiết từng lớp: [Harness](harness/README.md) · [Agent Loop](agent_loop/README.md) · [Session](harness/session/README.md)

## Cấu trúc folder

```
agent/
├── README.md           ← bạn đang ở đây
├── types.py            ← AgentContext, AgentLoopConfig, events, tools
├── events.py           ← AgentEventStream (async iterate + .result())
├── validation.py       ← validate tool arguments
├── agent_loop/         ← orchestration thuần (stateless)
└── harness/            ← orchestration + session + compaction
    ├── session/        ← persistence session tree
    ├── compaction.py   ← logic micro/full compact
    └── resources.py    ← format skill / prompt template
```

Catalog/resource stores và agent definition assembly nằm ngoài package này:
`agent_smith.resources` resolve definitions thành snapshot, còn `agent_smith.runtime`
compile `AgentDefinition` thành `AgentHarnessOptions`.

## Hai lớp orchestration

| | **agent_loop** | **harness** |
|---|----------------|-------------|
| State | Caller tự giữ messages | Session tree + TurnState |
| Entry | `run_agent_loop(...)` | `harness.prompt(...)` |
| Hooks | Callbacks trong `AgentLoopConfig` | Event/hook typed + session write |
| Context window | Không tự xử lý | Microcompact + LLM summary compact |
| Queues | steering / follow-up qua config | `steer()`, `follow_up()`, `next_turn()` |

Agent loop là **engine**; harness là **shell** bọc engine với lifecycle đầy đủ.

## Luồng một lần `prompt()` (tóm tắt)

```
prompt(text)
  → tạo TurnState (session + model + tools)
  → _execute_turn
       → run_agent_loop (xem agent_loop/README.md)
       → ghi message vào session theo event
       → prepare_next_turn: sync lại TurnState
  → trả assistant message cuối
```

## Shared types

- **`AgentMessage`** — user / assistant / toolResult (mở rộng từ `ai` messages)
- **`AgentTool`** — tool đăng ký cho loop
- **`AgentEvent`** — `agent_start/end`, `turn_start/end`, `message_*`, `tool_execution_*`
- **`StreamFn`** — inject cách gọi LLM (mặc định harness → `ai.stream_simple`)

## Liên quan package khác

- [`ai/README.md`](../ai/README.md) — gọi LLM, catalog model
- [`harness/session/README.md`](harness/session/README.md) — session tree, memory/postgres backend
