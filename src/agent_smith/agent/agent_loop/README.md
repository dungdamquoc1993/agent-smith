# Agent Loop

Orchestrate multi-turn agent: gọi LLM → chạy tool → lặp cho đến khi dừng. Consumer chính của `ai` trong production.

## Vị trí trong stack

```
AgentHarness.prompt()
       │
       ▼
  agent_loop              ← turn loop, tools, events
       │
       ├── streaming.py  → ai.stream_simple (mặc định)
       └── tools.py       → AgentTool.execute
```

Types/events nằm ở `agent/types.py`, `agent/events.py` — loop chỉ chứa logic chạy.

## Luồng một lần chạy

```
run_agent_loop(prompts, context, config)
       │
       ▼
  run_loop
       │
       ├─ steering messages (inject giữa turn)
       ├─ stream_assistant_response  → LLM, emit message_start/update/end
       ├─ execute_tool_calls         → sequential hoặc parallel
       ├─ prepare_next_turn            → refresh context/model (harness dùng cho session sync)
       ├─ should_stop_after_turn       → dừng sớm?
       └─ follow-up messages           → lặp outer loop nếu có
       │
       ▼
  AgentEndEvent → trả list message mới
```

**Inner loop:** stream → tool → stream → … cho đến khi assistant không còn tool call (hoặc batch `terminate`).

**Outer loop:** sau khi inner xong, lấy follow-up queue → chạy inner lại.

## Thành phần

| File | Vai trò |
|------|---------|
| **`runner.py`** | `run_agent_loop`, `run_loop`, entry `agent_loop` / `agent_loop_continue`. |
| **`streaming.py`** | Map `AgentContext` → `ai.Context`, gọi `stream_fn`, forward LLM events. |
| **`tools.py`** | Prepare/validate/execute tool, hooks before/after, emit tool events. |
| **`utils.py`** | `emit`, `call`, steering/follow-up helpers. |

## API

| Hàm | Dùng khi |
|-----|----------|
| **`run_agent_loop`** | Có prompt mới; append vào context rồi chạy. |
| **`run_agent_loop_continue`** | Context đã có messages; tiếp tục (last message ≠ assistant). |
| **`agent_loop` / `agent_loop_continue`** | Wrapper trả `AgentEventStream` (async iterate + `.result()`). |

Harness gọi trực tiếp `run_agent_loop` + event sink riêng.

## Config & hooks

`AgentLoopConfig` extends `SimpleStreamOptions` + `model` + callbacks:

- **`stream_fn`** — inject LLM (harness → `_default_stream_fn` → `stream_simple`)
- **`transform_context` / `convert_to_llm`** — chỉnh messages trước khi gọi LLM
- **`before_tool_call` / `after_tool_call`** — block/patch tool result
- **`prepare_next_turn`** — đổi context/model sau mỗi turn (harness flush session + rebuild state)
- **`get_steering_messages` / `get_follow_up_messages`** — queue inject message giữa/chỉ turn
- **`should_stop_after_turn`** — dừng loop sớm

## Events

Loop emit `AgentEvent`: `agent_start/end`, `turn_start/end`, `message_*`, `tool_execution_*`.

Harness map các event này sang hook riêng (session write, UI, …).

## Ví dụ

```python
from agent_smith.agent.agent_loop import run_agent_loop
from agent_smith.agent.types import AgentContext, AgentLoopConfig

new_messages = await run_agent_loop(
    prompts=[user_msg],
    context=AgentContext(system_prompt="...", messages=history, tools=tools),
    config=AgentLoopConfig(model=model, reasoning="low"),
    emit_event=on_event,
    stream_fn=stream_simple,  # optional
)
```
