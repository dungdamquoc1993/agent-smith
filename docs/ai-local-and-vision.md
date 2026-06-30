# AI layer: local LLM/VLM & vision

Tóm tắt nhanh về `src/ai` — local inference và xử lý ảnh.

## `src/ai` là gì?

Client library gọi model qua **LiteLLM**, không phải HTTP server tự host LLM.

```
AgentHarness → agent_loop → stream_simple → LitellmApiProvider → litellm.acompletion()
```

## Dùng local LLM / vLLM

Catalog mặc định (`models.catalog.json`) chỉ có cloud (OpenAI, Anthropic, Google). Local cần **đăng ký model**:

```python
from ai import bootstrap_providers, register_model, make_litellm_model

bootstrap_providers()

model = make_litellm_model(
    provider="openai",
    model_id="qwen2-vl-7b",
    litellm_model="openai/qwen2-vl-7b",
    base_url="http://localhost:8000/v1",  # vLLM / LM Studio (OpenAI-compatible)
    input=["text", "image"],              # bắt buộc nếu model là VLM
)
register_model(model)
```

- `base_url` → LiteLLM `api_base`
- Muốn **nhìn ảnh** → model trên server phải là **VLM**, không phải LLM text-only
- Không có pattern sẵn “LLM text gọi VLM riêng” — dùng 1 VLM làm model chính, hoặc tự viết tool

## Ảnh được hỗ trợ ở đâu?

| Nguồn | Gửi lên model? |
|-------|----------------|
| User message / `prompt(..., images=[...])` | ✅ `ImageContent` → `image_url` base64 |
| Tool result (`ToolResultMessage`) | ❌ Chỉ text được gửi |

Tool/MCP vẫn **lưu** ảnh trong session; lúc build payload LiteLLM thì ảnh trong tool result **bị bỏ qua** (`litellm_provider.py`, nhánh `ToolResultMessage`).

**Workaround:** tool trả mô tả text / OCR thay vì chỉ ảnh. **Fix đúng:** mở rộng `_context_to_litellm_messages` (có thể phải map sang user multimodal tùy provider).

## Liên quan

- [`src/ai/README.md`](../src/ai/README.md) — overview package
- [`src/ai/providers/litellm_provider.py`](../src/ai/providers/litellm_provider.py) — convert messages → LiteLLM
