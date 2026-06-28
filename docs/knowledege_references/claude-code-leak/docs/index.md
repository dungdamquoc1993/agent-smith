# Claude Code Source — Ghi chú học tập

Tài liệu này ghi lại những gì đã tìm hiểu từ bộ source code leak của Claude Code.

## Mục lục

- [tools.md](./tools.md) — Hệ thống tools: danh sách đầy đủ, cơ chế load, các bộ tools theo context
- [multi-agent.md](./multi-agent.md) — Hai hệ multi-agent: Subagent (background agents) vs Swarm (agent teams)

## Cấu trúc source

```
src/
├── tools/          — Mỗi tool nằm trong thư mục riêng (tên tool + Tool)
├── tools.ts        — Master registry: getAllBaseTools(), getTools(), assembleToolPool()
├── constants/
│   └── tools.ts    — Các tập hợp tool theo context (Agent, Coordinator, Teammate...)
├── main.tsx        — Entry point, nơi tools được inject vào session
└── utils/
    ├── agentSwarmsEnabled.ts   — Gate cho tính năng multi-agent
    └── toolSearch.ts           — Gate cho ToolSearch
```

## Lưu ý quan trọng

- Nhiều tools trong source (PushNotificationTool, SendUserFileTool, WebBrowserTool...) **không có trong leak này** — chúng được gate bởi build-time flags và chưa được Anthropic public.
- `feature('...')` là build-time bundler flag (Bun), không phải runtime check — quyết định ngay khi bundle.
- `process.env.USER_TYPE === 'ant'` là cờ nhận diện môi trường nội bộ Anthropic.
