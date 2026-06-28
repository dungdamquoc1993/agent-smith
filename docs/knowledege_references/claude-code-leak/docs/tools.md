# Hệ thống Tools trong Claude Code

## Kiến trúc tổng quan

```
getAllBaseTools()          ← Master list toàn bộ tools (source of truth)
        ↓
    getTools()            ← Filter runtime → bộ tools thực tế cho interactive session
        ↓
assembleToolPool()        ← getTools() + MCP tools (dedup, sort ổn định cho prompt cache)
```

- **`getAllBaseTools()`** — không lược bớt gì, chỉ quyết định import dựa trên build-time flags
- **`getTools()`** — filter theo deny rules → REPL mode → `isEnabled()` của từng tool
- **`assembleToolPool()`** — dùng cho interactive session, ghép thêm MCP tools

Source: [src/tools.ts](../src/tools.ts), [src/constants/tools.ts](../src/constants/tools.ts)

---

## Bộ tools LUÔN có (mọi môi trường)

Đây là phần cứng của `getAllBaseTools()` — không phụ thuộc flag nào.

| Tool (tên nội bộ) | Công dụng |
|---|---|
| **Agent** | Spawn subagent để delegate công việc |
| **Bash** | Chạy lệnh shell |
| **Read** (`str_replace_based_edit`) | Đọc file, ảnh, PDF, notebook |
| **Edit** | Chỉnh sửa file tại chỗ (dạng diff) |
| **Write** | Tạo hoặc ghi đè file |
| **Glob** | Tìm file theo pattern (`**/*.ts`) |
| **Grep** | Tìm nội dung file bằng regex (ripgrep) |
| **WebFetch** | Fetch và trích xuất nội dung từ URL |
| **WebSearch** | Tìm kiếm web |
| **TodoWrite** | Quản lý task checklist trong session |
| **NotebookEdit** | Chỉnh sửa cell trong Jupyter notebook |
| **TaskOutput** | Đọc output/log từ background task |
| **TaskStop** | Dừng một background task đang chạy |
| **AskUserQuestion** | Hỏi user dạng multiple-choice |
| **Skill** | Gọi slash-command skill |
| **EnterPlanMode** | Chuyển sang plan mode (chỉ thiết kế, không code) |
| **ExitPlanMode** | Thoát plan mode, trình bày kế hoạch để phê duyệt |
| **SendMessage** | Gửi tin nhắn đến agent teammate (luôn có trong registry nhưng `isEnabled()` gate theo swarm) |
| **ListMcpResources** | Liệt kê resources từ MCP servers đang kết nối |
| **ReadMcpResource** | Đọc một MCP resource theo URI |

> **Lưu ý:** `GlobTool` và `GrepTool` bị **ẩn** trong ant native build — thay thế bằng `bfs/ugrep` nhúng trực tiếp vào binary, nhanh hơn.

---

## Cơ chế phân tầng — Yếu tố ảnh hưởng bộ tools được load

### Tầng 1 — Build-time flags (`feature('...')`)

Quyết định khi **bundle**, không thể thay đổi lúc runtime. Dùng Bun bundler.

| Flag | Tools được bật | Ghi chú |
|---|---|---|
| `PROACTIVE` hoặc `KAIROS` | `Sleep` | Proactive/autonomous mode |
| `KAIROS` | `SendUserFile`, `PushNotification` | Chưa có trong leak |
| `KAIROS_PUSH_NOTIFICATION` | `PushNotification` | Chưa có trong leak |
| `KAIROS_GITHUB_WEBHOOKS` | `SubscribePR` | Chưa có trong leak |
| `KAIROS_BRIEF` | `Brief` | Cùng gate với `KAIROS` |
| `AGENT_TRIGGERS` | `CronCreate`, `CronDelete`, `CronList` | |
| `AGENT_TRIGGERS_REMOTE` | `RemoteTrigger` | |
| `MONITOR_TOOL` | `Monitor` | Chưa có trong leak |
| `WEB_BROWSER_TOOL` | `WebBrowser` | Chưa có trong leak |
| `COORDINATOR_MODE` | coordinator module | Multi-agent coordinator |
| `WORKFLOW_SCRIPTS` | `Workflow` | Chưa có trong leak |
| `UDS_INBOX` | `ListPeers` | Unix domain socket inbox |
| `OVERFLOW_TEST_TOOL` | `OverflowTest` | Chưa có trong leak |
| `CONTEXT_COLLAPSE` | `CtxInspect` | Chưa có trong leak |
| `TERMINAL_PANEL` | `TerminalCapture` | Chưa có trong leak |
| `HISTORY_SNIP` | `Snip` | Chưa có trong leak |

### Tầng 2 — Runtime environment variables

| Điều kiện | Tools thêm/bớt |
|---|---|
| `USER_TYPE === 'ant'` (Anthropic internal) | Thêm: `Config`, `Tungsten`, `REPL`, `SuggestBackgroundPR`; `Agent` được phép nested |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=true` + GrowthBook flag `tengu_amber_flint` | Thêm: `TeamCreate`, `TeamDelete`; `SendMessage` bật `isEnabled()` |
| `ENABLE_LSP_TOOL=true` | Thêm: `LSP` (Language Server Protocol) |
| `CLAUDE_CODE_VERIFY_PLAN=true` | Thêm: `VerifyPlanExecution` (chưa có trong leak) |
| `CLAUDE_CODE_SIMPLE=true` | Chỉ còn: `[Bash, Read, Edit]` |
| Windows + PowerShell available | Thêm: `PowerShell` |
| `isWorktreeModeEnabled()` | Thêm: `EnterWorktree`, `ExitWorktree` |
| `isToolSearchEnabled()` | Thêm: `ToolSearch` |
| `isTodoV2Enabled()` | Thêm: `TaskCreate`, `TaskGet`, `TaskUpdate`, `TaskList` |
| `NODE_ENV === 'test'` | Thêm: `TestingPermission` |

### Tầng 3 — Mỗi tool tự kiểm tra `isEnabled()`

Runtime check cuối cùng, chạy mỗi lần `getTools()` được gọi:

| Tool | Điều kiện `isEnabled()` |
|---|---|
| `SendMessage` | `isAgentSwarmsEnabled()` |
| `TeamCreate`, `TeamDelete` | `isAgentSwarmsEnabled()` |
| `Sleep` | `isProactiveActive()` |
| `Brief` | `(getKairosActive() \|\| getUserMsgOptIn()) && isBriefEntitled()` |
| `LSP` | `isEnvTruthy(ENABLE_LSP_TOOL)` |
| `EnterWorktree`, `ExitWorktree` | `isWorktreeModeEnabled()` |
| `ToolSearch` | `isToolSearchEnabledOptimistic()` |
| `CronCreate/Delete/List` | built-in true (đã gate ở tầng 1) |

---

## Bộ tools theo context (agent type)

Sau khi `getTools()` trả về bộ đầy đủ cho session, các sub-context lọc tiếp:

### Interactive session (người dùng thông thường)
→ Toàn bộ `getTools()` + MCP tools từ config

### Async Agent (được spawn bởi `Agent` tool)
Chỉ được dùng `ASYNC_AGENT_ALLOWED_TOOLS`:
```
Read, WebSearch, TodoWrite, Grep, WebFetch, Glob,
Bash/PowerShell, Edit, Write, NotebookEdit,
Skill, StructuredOutput, ToolSearch,
EnterWorktree, ExitWorktree
```

### In-process Teammate (swarm member)
`ASYNC_AGENT_ALLOWED_TOOLS` **cộng thêm** `IN_PROCESS_TEAMMATE_ALLOWED_TOOLS`:
```
TaskCreate, TaskGet, TaskList, TaskUpdate,
SendMessage,
CronCreate, CronDelete, CronList  ← nếu AGENT_TRIGGERS bật
```

### Coordinator Mode
Chỉ 4 tools:
```
Agent, TaskStop, SendMessage, StructuredOutput
```

### SIMPLE mode (`CLAUDE_CODE_SIMPLE=true`)
Chỉ 3 tools:
```
Bash, Read, Edit
```
(+ `Agent`, `TaskStop`, `SendMessage` nếu Coordinator Mode cũng bật)

### SDK / non-interactive session
`getTools()` bình thường + thêm `StructuredOutput` (`SyntheticOutputTool`) sau khi getTools() chạy xong. Tool này ép Claude trả JSON đúng schema.

---

## Tools chỉ trong source, không có trong leak (build-time gates)

Những tools này tồn tại trong production build của Anthropic nhưng **không được include trong bản leak**. Được nhắc đến trong `tools.ts` qua `require()` conditional:

| Tool | Feature flag | Mô tả suy đoán |
|---|---|---|
| `PushNotificationTool` | `KAIROS` / `KAIROS_PUSH_NOTIFICATION` | Gửi push notification đến user |
| `SendUserFileTool` | `KAIROS` | Gửi file cho user trong proactive mode |
| `SubscribePRTool` | `KAIROS_GITHUB_WEBHOOKS` | Subscribe webhook GitHub PR |
| `WebBrowserTool` | `WEB_BROWSER_TOOL` | Điều khiển browser headless |
| `WorkflowTool` | `WORKFLOW_SCRIPTS` | Chạy workflow scripts |
| `MonitorTool` | `MONITOR_TOOL` | Monitor output background process |
| `SnipTool` | `HISTORY_SNIP` | Cắt tỉa conversation history |
| `CtxInspectTool` | `CONTEXT_COLLAPSE` | Inspect/collapse context |
| `TerminalCaptureTool` | `TERMINAL_PANEL` | Capture terminal output |
| `OverflowTestTool` | `OVERFLOW_TEST_TOOL` | Testing tool |
| `ListPeersTool` | `UDS_INBOX` | List UDS/bridge peers |
| `TungstenTool` | `USER_TYPE === 'ant'` | Internal Anthropic tool |
| `SuggestBackgroundPRTool` | `USER_TYPE === 'ant'` | Đề xuất tạo PR nền |
| `VerifyPlanExecutionTool` | `CLAUDE_CODE_VERIFY_PLAN=true` | Xác minh thực thi plan |

---

## ToolSearch — cơ chế deferred tools

### Nguyên lý

Thay vì inject schema của tất cả tools vào `tools[]` param ngay từ đầu (tốn token), Claude Code **defer** một số tools — chỉ liệt kê tên, không gửi schema. Model cần dùng tool nào thì gọi `ToolSearch` để load schema.

Quy tắc defer (hàm `isDeferredTool()`):
- **MCP tools** — luôn defer (quá nhiều, workflow-specific)
- **`shouldDefer: true`** — tool tự khai báo muốn defer
- **Ngoại lệ không bao giờ defer:** `ToolSearch` bản thân, `Brief`, `Agent` (khi FORK_SUBAGENT), `SendUserFile`

### Tool result trả về `tool_reference` — không phải text hay schema

Khi ToolSearch tìm thấy tool, nó trả về **content block đặc biệt** (beta API type, không có trong SDK types chính thức):

```json
{
  "type": "tool_result",
  "tool_use_id": "...",
  "content": [
    { "type": "tool_reference", "tool_name": "WebFetch" },
    { "type": "tool_reference", "tool_name": "Grep" }
  ]
}
```

### Server tự expand — không phải client inject vào `tools[]`

Khi API nhận message history có `tool_reference` block, **server-side tự inject full JSON schema** của tool đó vào context của model. Client không cần thêm gì vào `tools[]` param của lượt tiếp theo.

> Source: *"The API expands these references into full tool definitions in the model's context."*

Vì cơ chế này dựa vào server, nó **chỉ hoạt động với Anthropic 1P API**. Proxy/gateway bên thứ 3 (trừ LiteLLM passthrough và một số trường hợp đặc biệt) có thể reject `tool_reference` block với lỗi 400 — đó là lý do ToolSearch bị tắt mặc định khi `ANTHROPIC_BASE_URL` trỏ sang host không phải Anthropic.

### Cách deferred tools được thông báo cho model

Có 2 chế độ (kiểm soát bởi GrowthBook flag `tengu_glacier_2xr`):

| Chế độ | Điều kiện | Cách thông báo |
|---|---|---|
| **Delta / system-reminder** | `USER_TYPE === 'ant'` hoặc flag `tengu_glacier_2xr` | Inject qua `<system-reminder>` message mỗi lượt — chỉ thông báo phần thay đổi (added/removed) |
| **Legacy / available-deferred-tools** | External default | Prepend block `<available-deferred-tools>` vào đầu mỗi API call — liệt kê toàn bộ |

Cả hai đều chỉ liệt kê **tên tool**, không có schema. (A/B test `exp_xenhnnmn0smrx4` từng thử thêm `searchHint` vào danh sách nhưng kết quả không có lợi — đã dừng từ Mar 2021.)

### Client theo dõi tools đã discovered

Client scan message history qua `extractDiscoveredToolNames()` để biết tool nào đã có `tool_reference` block. Khi context bị **compact** (rút gọn lịch sử), danh sách này được snapshot vào `compactMetadata.preCompactDiscoveredTools` để không mất.

### Flow đầy đủ

```
Lượt 1 — API call
  tools[]  = [ToolSearch, Bash, Edit, ...]    ← non-deferred: full schema
  system   = "<system-reminder> deferred: WebFetch, Grep, SendMessage ..."
                                               ← deferred: chỉ tên

  Model gọi: ToolSearch({ query: "select:WebFetch,Grep" })

Lượt 2 — tool_result
  content = [
    { type: "tool_reference", tool_name: "WebFetch" },
    { type: "tool_reference", tool_name: "Grep" }
  ]
  → Server expand full schema của 2 tools vào context
  → Client ghi nhận vào discoveredTools set

Lượt 3
  Model gọi WebFetch / Grep bình thường
```

### Tại sao intercept traffic không thấy ToolSearch

Haiku không support `tool_reference` nên ToolSearch bị disable khi dùng Haiku. Với các model khác, ToolSearch vẫn xuất hiện trong `tools[]` nhưng các deferred tools thì **không** — schema của chúng không bao giờ nằm trong `tools[]` param, chỉ được server inject ngầm sau khi có `tool_reference` block trong history.
