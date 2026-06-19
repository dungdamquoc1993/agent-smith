# AI

Lớp gọi LLM thống nhất: types chung, catalog model, registry provider, stream events.

## Vị trí trong stack

Luồng production chính **không** phải harness → ai trực tiếp:

```
AgentHarness.prompt()
       │
       ▼
  agent_loop          ← orchestrate turn: stream → tools → lặp
       │
       ▼
  ai.stream_simple    ← mặc định (hoặc stream_fn inject)
```

**Ai gọi `ai`?**

| Caller | Cách dùng |
|--------|-----------|
| **`agent_loop`** | Gọi chính: `stream_simple` mỗi turn (`streaming.py`). |
| **`AgentHarness`** | Chủ yếu qua `run_agent_loop`; default `stream_fn` = `stream_simple`. Trực tiếp `complete_simple` khi **compaction** (summarize, không qua loop). |
| **Harness / agent khác** | Chủ yếu import **types** (`Model`, `Message`, …) cho tường minh. |
| **Tests / examples** | Gọi `stream` / `complete` trực tiếp. |

## Luồng nội bộ package

```
api.py               ← stream / complete (+ _simple variants)
       │
       ├── models.py   ← resolve Model (catalog)
       ├── env_keys.py ← API key từ env (nếu chưa truyền)
       │
       ▼
  registry.py          ← lookup theo Model.api
       │
       ▼
  ApiProvider          ← hiện tại: LitellmApiProvider
       │
       ▼
  AssistantMessageEventStream   ← async events → done/error → .result()
```

## Thành phần

| File | Vai trò |
|------|---------|
| **`types.py`** | Contract: `Model`, `Context`, `Message`, content blocks, stream options, events. |
| **`models.py`** | Catalog model (`models.catalog.json`), `get_model`, `register_model`. |
| **`registry.py`** | Đăng ký/lookup `ApiProvider` theo `Model.api`. |
| **`api.py`** | Entry point công khai. |
| **`events.py`** | `AssistantMessageEventStream` — queue events, iterate, lấy kết quả cuối. |
| **`providers/`** | Implementation backend (LiteLLM). |
| **`env_keys.py`** | Map provider → env var API key. |

## Input / Output

**Vào:** `Model` + `Context` (system prompt, messages, tools) + `StreamOptions`

**Ra:** stream events (`text_delta`, `toolcall_*`, `thinking_*`, …) → `AssistantMessage` cuối cùng

- **`stream` / `complete`** — full options; reasoning map qua `Model.thinking_level_map`
- **`stream_simple` / `complete_simple`** — wrapper gọn, truyền `reasoning` trực tiếp

## Model vs Provider

- **`Model.provider`** — vendor logic (openai, anthropic, google, …): auth, pricing, compat
- **`Model.api`** — transport adapter (`litellm`): chọn implementation trong registry
- **`Model.litellm_model`** — id gửi sang LiteLLM (vd. `openai/gpt-4o`)

## Khởi tạo

Dùng trực tiếp (test, script) hoặc qua agent loop (production):

```python
from agent_smith.ai import bootstrap_providers, get_model, stream, Context

bootstrap_providers()  # đăng ký litellm; catalog load sẵn lúc import models

model = get_model("openai", "gpt-5.5")
event_stream = stream(model, Context(system_prompt="...", messages=[...]))

async for event in event_stream:
    ...  # text_delta, toolcall_*, done, error

msg = await event_stream.result()  # hoặc dùng complete() trực tiếp
```

Trong harness, turn thường đi `run_agent_loop(..., stream_fn=stream_simple)` — không cần gọi `ai` ở layer harness.

## Mở rộng

Thêm backend mới: implement `ApiProvider` → `register_api_provider()`. Agent loop/harness không cần đổi nếu vẫn dùng `stream_simple` / types chung.
