# MCP trong Claude Code — Tổng hợp kiến thức

Tài liệu gom toàn bộ kiến thức về MCP (Model Context Protocol) trong Claude Code: tool layer, service layer, config, runtime state, và các mechanism phụ (auth, elicitation, prompts, resources).

Nguồn tham chiếu: `docs/khowledge_references/claude-code/services/mcp/`, `tools/MCPTool/`, `tools/McpAuthTool/`, v.v.

---

## 1. Bức tranh tổng thể

Claude Code đóng vai **MCP Client**. User cấu hình nhiều MCP server; mỗi server là một connection độc lập expose các capability theo MCP protocol.

```
Config sources
      ↓
services/mcp/  (connect, auth, fetch, call)
      ↓
Runtime state  (clients, tools, commands, resources)
      ↓
Agent          (model nhận tool list, gọi tool)
      ↓
services/mcp/client.ts  (forward tools/call về MCP server)
```

MCP protocol trên **một connection** có thể expose nhiều loại capability:

| Capability | Protocol | Claude Code map thành |
|------------|----------|----------------------|
| **Tools** | `tools/list`, `tools/call` | Dynamic instances từ `MCPTool` template |
| **Resources** | `resources/list`, `resources/read` | `ListMcpResourcesTool` + `ReadMcpResourceTool` |
| **Prompts** | `prompts/list`, `prompts/get` | Slash commands (không phải Tool) |

---

## 2. Tool layer — những gì model "thấy"

### 2.1. Bốn tool class implementation

| Tool class | Vai trò |
|------------|---------|
| **`MCPTool`** | Template/base cho mọi tool động từ MCP server. Naming: `mcp__<server>__<tool>` |
| **`McpAuthTool`** | Pseudo-tool khi server cần OAuth: `mcp__<server>__authenticate`. Tạo dynamic per server qua `createMcpAuthTool()` |
| **`ListMcpResourcesTool`** | Browse MCP resources từ connected servers |
| **`ReadMcpResourceTool`** | Đọc nội dung resource theo `{ server, uri }` |

Dynamic MCP tools **không có file riêng** — mỗi tool server expose là instance runtime clone từ `MCPTool`, được tạo trong `fetchToolsForClient()` (`client.ts`).

### 2.2. `ToolSearchTool` — không phải MCP tool, nhưng liên quan chặt

`ToolSearchTool` search **toàn bộ deferred tools**, không chỉ MCP.

Claude Code có hai lớp tool:

| Lớp | Ý nghĩa | Ví dụ |
|-----|---------|-------|
| **Loaded ngay** | Full JSON schema gửi model từ đầu | `Bash`, `Read`, `Edit`, `Grep`, `ToolSearchTool` |
| **Deferred ("ẩn")** | Chỉ có **tên** trong prompt; chưa có schema → **chưa gọi được** | Hầu hết MCP tools + nhiều built-in |

Logic defer (`isDeferredTool()` trong `tools/ToolSearchTool/prompt.ts`):

- MCP tools → **luôn deferred** (trừ `_meta['anthropic/alwaysLoad']`)
- Built-in → deferred nếu `shouldDefer: true` (`WebSearch`, `TodoWrite`, `LSPTool`, `ListMcpResourcesTool`, …)
- `ToolSearchTool` → **không bao giờ deferred** (model cần nó để load tools khác)

Flow: model thấy tên → gọi `ToolSearchTool(query=...)` → nhận full schema → mới gọi được tool đó.

Lý do: tiết kiệm context window khi user cắm nhiều MCP server (hàng trăm tool schema rất nặng).

### 2.3. MCP Resources — khác MCP Tools

| | **MCP Tools** | **MCP Resources** |
|---|---------------|-------------------|
| Bản chất | Hành động callable | Dữ liệu read-only theo URI |
| Protocol | `tools/list` → `tools/call` | `resources/list` → `resources/read` |
| Server bắt buộc? | Thường có | Optional — nhiều server chỉ có tools |

`ListMcpResourcesTool` và `ReadMcpResourceTool` là **wrapper built-in của Claude Code** — model không gọi MCP JSON-RPC trực tiếp; gọi tool Claude Code, tool đó forward sang MCP client.

Hai tool này chỉ được thêm **một lần globally** khi gặp server đầu tiên có `capabilities.resources`.

---

## 3. `services/mcp/` — phần lõi

Đây là nơi MCP "thực sự chạy". Tool classes chỉ là lớp mỏng phía agent.

### 3.1. Các file chính

| File / module | Vai trò |
|---------------|---------|
| **`config.ts`** | Đọc/merge/validate config từ nhiều nguồn; enable/disable server |
| **`client.ts`** | Connect transport, fetch capabilities, wrap tools, gọi `tools/call`, transform result |
| **`useManageMCPConnections.ts`** | Lifecycle orchestrator: khởi động connections, sync state, reconnect, handle notifications |
| **`auth.ts`** | OAuth discovery, token storage/refresh, XAA (cross-app access) |
| **`types.ts`** | Config schemas + connection state types |
| **`utils.ts`, `normalization.ts`, `mcpStringUtils.ts`** | Naming, helpers |
| **`headersHelper.ts`, `envExpansion.ts`** | Headers động, expand env vars trong config |
| **`elicitationHandler.ts`** | Xử lý khi MCP server hỏi client (URL/form elicitation) |
| **`claudeai.ts`** | MCP connectors từ claude.ai |
| **`InProcessTransport.ts`, `SdkControlTransport.ts`** | Transport in-process (Chrome MCP, SDK) |
| **`channelNotification.ts`, `channelPermissions.ts`** | Push notification từ MCP channel servers |

### 3.2. Connection states

Mỗi server là một `MCPServerConnection` (`types.ts`):

| State | Ý nghĩa |
|-------|---------|
| `pending` | Đang connect (thường claude.ai connectors) |
| `connected` | OK — có MCP SDK `Client` + `capabilities` |
| `needs-auth` | Cần OAuth → expose `McpAuthTool` |
| `failed` | Connect lỗi |
| `disabled` | User tắt server |

---

## 4. Config — server nào, connect thế nào

Config quyết định **server nào được bật** và **tham số connect từng server**.

### 4.1. Nguồn config (merge theo priority)

1. **`managed-mcp.json`** (enterprise) — nếu có thì exclusive, bỏ claude.ai
2. **Claude Code local**: `.mcp.json`, user settings, plugins
3. **`claude.ai connectors`** — priority thấp nhất, dedup nếu trùng URL
4. **`--mcp-config`** — dynamic, truyền lúc chạy

`getAllMcpConfigs()` merge → `Record<serverName, ScopedMcpServerConfig>`.

Mỗi server có `scope`: `local`, `project`, `user`, `plugin`, `claudeai`, `enterprise`, `dynamic`, `managed`, …

### 4.2. Config per-server

Ví dụ cấu trúc:

```json
{
  "mcpServers": {
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "..." }
    },
    "remote-api": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "X-Api-Key": "..." },
      "oauth": { "clientId": "...", "callbackPort": 8080 }
    }
  }
}
```

Transport types: `stdio`, `http`, `sse`, `ws`, `sse-ide`, `ws-ide`, `claudeai-proxy`, `sdk`, …

### 4.3. Enable / disable

- Server thường: **opt-out** — có trong config → connect, trừ khi nằm trong `disabledMcpServers`
- Built-in mặc định tắt (Chrome MCP…): **opt-in** qua `enabledMcpServers`
- Toggle qua `/mcp` → `setMcpServerEnabled()`

Active = có trong merged config **và** không bị disable.

---

## 5. Luồng khởi động

### 5.1. Orchestrator

`useManageMCPConnections` (interactive) hoặc `prefetchAllMcpResources` / `getMcpToolsCommandsAndResources` (headless) là entry point.

Hai phase:

- **Phase 1**: Connect server local/plugin ngay
- **Phase 2**: Fetch claude.ai connectors (có thể chậm hơn)

Local server (`stdio`, `sdk`) connect với concurrency thấp. Remote (`http`, `sse`, `ws`) concurrency cao hơn.

### 5.2. `getMcpToolsCommandsAndResources` — vừa connect vừa fetch

Tên hàm dễ gây hiểu nhầm. Thực tế **không fetch trước khi connect**.

Với mỗi server, `processServer()` trong `client.ts`:

```
disabled?           → callback, không connect
cached needs-auth?  → callback với McpAuthTool, không connect
connectToServer()   → CONNECT
  ├ fail auth       → needs-auth + McpAuthTool
  ├ fail other      → failed
  └ ok              → parallel fetch:
       tools/list       → MCPTool instances
       prompts/list     → slash commands
       resources/list   → cache
       + List/ReadMcpResourceTool nếu có resources
onConnectionAttempt({ client, tools, commands, resources })
```

`onConnectionAttempt` là **callback sau mỗi lần state thay đổi**, không phải "attempt trước connect".

Timeline:

- T0: start
- T1–T2: connect từng server async
- T3: server connected → fetch → callback → cập nhật state
- T4: agent turn → đọc tools từ state → merge vào context model

Server chưa connected → không có tools. Tools xuất hiện dần khi từng server connect xong.

### 5.3. Cập nhật state — replace theo prefix

Khi server reconnect/update, tools cũ của server đó bị xóa theo prefix `mcp__<server>__*`, rồi gắn tools mới. Tránh stale tools.

Logic nằm trong `updateServer()` / `flushPendingUpdates()` của `useManageMCPConnections.ts`.

---

## 6. `connectToServer()` — transport layer

`connectToServer` được memoize theo `(name, config)`.

| Transport | Cách connect |
|-----------|--------------|
| `stdio` | Spawn subprocess (`StdioClientTransport`) |
| `http` | Streamable HTTP + OAuth (`ClaudeAuthProvider`) |
| `sse` | Server-Sent Events + OAuth |
| `ws` | WebSocket |
| `sse-ide` / `ws-ide` | IDE extension |
| `claudeai-proxy` | Proxy qua claude.ai OAuth |
| `sdk` | In-process qua SDK transport |
| In-process stdio | Chrome MCP, Computer Use — `InProcessTransport`, không spawn subprocess |

Sau khi tạo transport:

1. `new Client(...)` với capabilities `roots`, `elicitation`
2. Handler `ListRoots` → trả workspace root (`file://cwd`)
3. `client.connect(transport)` với timeout
4. Đọc `capabilities`, `serverVersion`, `instructions`
5. Gắn error/close handlers → trigger reconnect

401/Unauthorized → `needs-auth` thay vì crash.

Client cũng khai báo capability **`elicitation`** để server có thể hỏi ngược lại.

---

## 7. Sau connect — fetch 3 loại capability

### 7.1. Tools

`fetchToolsForClient()` gọi `tools/list` → mỗi tool map thành object clone `MCPTool` với:

- `name`: `mcp__server__tool` (hoặc tên gốc nếu SDK no-prefix mode)
- `mcpInfo`: `{ serverName, toolName }`
- `inputJSONSchema` từ server
- `.call()` → `ensureConnectedClient()` → `callMCPToolWithUrlElicitationRetry()`

`fetchToolsForClient` được LRU-cache theo server name. Invalidate khi `onclose` hoặc `tools/list_changed` notification.

### 7.2. Prompts → slash commands

`fetchCommandsForClient()` gọi `prompts/list` → convert thành `Command` objects:

- `type: 'prompt'`
- `source: 'mcp'`
- `userFacingName`: `server:promptname (MCP)`
- `getPromptForCommand(args)` → gọi `prompts/get` trên MCP server → trả message blocks inject vào conversation

**Không phải Tool** — user gõ `/server:prompt (MCP) args`, hoặc model invoke qua `SkillTool` (MCP skills).

### 7.3. Resources

`fetchResourcesForClient()` gọi `resources/list` → cache trong state. `ReadMcpResourceTool` gọi `resources/read` trực tiếp khi model cần nội dung.

---

## 8. Runtime — model gọi MCP tool

```
Model gọi mcp__slack__send_message(args)
  → MCPTool.call()
  → ensureConnectedClient()  (reconnect nếu session expired)
  → callMCPToolWithUrlElicitationRetry()
  → client.callTool({ name, arguments })  // MCP SDK
  → MCP Server thực thi
  → transformMCPResult()  (truncate, persist binary blobs ra disk)
  → tool_result trả về model
```

Chi tiết `callMCPTool()`:

- Timeout riêng (race với SDK timeout)
- Progress callback cho long-running tools
- `isError: true` → `McpToolCallError`
- Binary content → lưu disk, trả path thay vì base64 vào context
- URL elicitation → pause, hỏi user, retry

HTTP session expiry: server trả 404 + JSON-RPC `-32001` → clear cache → reconnect lần sau.

---

## 9. Auth

### 9.1. Cached needs-auth

**Không phải cache OAuth token.**

- File local: `~/.claude/mcp-needs-auth-cache.json`
- TTL: **15 phút**
- Nội dung: `{ "serverName": { "timestamp": ... } }`
- Mục đích: nhớ "server vừa fail auth, đừng probe lại liên tục"

OAuth token thật lưu **secure storage** (macOS Keychain, …) qua `ClaudeAuthProvider`.

### 9.2. Nhiều kiểu auth — per transport

| Transport | Auth |
|-----------|------|
| `stdio` | Env vars trong config |
| `http` / `sse` | MCP OAuth (RFC) + optional static `headers` |
| `claudeai-proxy` | Claude.ai OAuth bearer |
| `sse-ide` / `ws-ide` | IDE auth token |
| Session ingress | JWT bearer cho remote MCP |

Không có router magic "JWT vs cookie" — mỗi transport xử lý theo config type.

### 9.3. `ClaudeAuthProvider`

Class implement **`OAuthClientProvider`** interface của MCP SDK — tên "Claude" chỉ là implementation của Claude Code, **không phải** "auth bởi Anthropic thay MCP server".

Flow OAuth MCP:

1. MCP server (resource server) yêu cầu auth
2. Client discover authorization server metadata (RFC 9728 → RFC 8414)
3. OAuth flow → token lưu keychain per server
4. SDK attach `Authorization: Bearer` vào requests
5. 401 → `needs-auth` → `McpAuthTool` → user authorize → `reconnectMcpServerImpl()` → swap pseudo-tool bằng real tools

`McpAuthTool` khi được gọi: `performMCPOAuthFlow()` → trả auth URL → user mở browser → callback background → reconnect.

---

## 10. Elicitation

Trong MCP, **server có thể hỏi client** (ngược chiều `tools/call`):

- **Form elicitation**: server cần thêm input từ user
- **URL elicitation**: server yêu cầu user mở URL (xác nhận, login web, …)

Client khai báo `elicitation: {}` lúc connect.

`registerElicitationHandler()` gắn handler → queue vào `AppState.elicitation.queue` → UI hiện prompt → user action → trả `ElicitResult` → tool call retry.

Dùng trong `callMCPToolWithUrlElicitationRetry()` khi tool cần user interaction giữa chừng.

---

## 11. Reconnect & cache

| Mechanism | Mục đích |
|-----------|----------|
| `connectToServer` memoize | Reuse connection healthy |
| `fetchToolsForClient` LRU cache | Tránh `tools/list` mỗi lần |
| `fetchResourcesForClient` LRU cache | Tương tự resources |
| `fetchCommandsForClient` LRU cache | Tương tự prompts |
| `onclose` handler | Clear cache, reconnect với exponential backoff (max 5 lần, 1s→30s) |
| `tools/list_changed` notification | Server đổi tools → refresh tự động |
| `resources/list_changed` | Refresh resources |
| `prompts/list_changed` | Refresh commands |

Local vs remote servers xử lý reconnect khác nhau (stdio process die vs SSE/HTTP stream drop).

---

## 12. AppState — runtime store

`AppState` là **global in-memory store**, không chỉ cho UI. Claude Code dùng React/Ink cho TUI nên lifecycle được orchestrate qua hooks — đó là implementation detail, không phải requirement của MCP.

Phần MCP trong AppState (`state/AppStateStore.ts`):

```typescript
mcp: {
  clients: MCPServerConnection[]   // connection status per server
  tools: Tool[]                    // dynamic MCP tools (MCPTool instances)
  commands: Command[]              // MCP prompts as slash commands
  resources: Record<string, ServerResource[]>
  pluginReconnectKey: number       // trigger re-connect khi reload plugins
}
```

AppState **không** nắm message history — chỉ nắm **runtime resources** (tools available, connection status, …). Message history ở layer session/REPL riêng.

Flow vào agent:

```
AppState.mcp.tools
  → useMergedTools() / assembleToolPool()
  → filter by permission rules (deny mcp__server__*, etc.)
  → gửi lên model cùng system prompt
```

Khi model gọi MCP tool, execution đọc `mcpClients` từ `ToolUseContext.options` (sync từ AppState).

Headless/print mode: không dùng React hooks — gọi thẳng `getMcpToolsCommandsAndResources()` / `prefetchAllMcpResources()`.

---

## 13. Những thứ liên quan MCP nhưng không phải Tool class

| Thành phần | Vai trò |
|------------|---------|
| **`ToolSearchTool`** | Load schema deferred tools (MCP + built-in) |
| **Dynamic `MCPTool` instances** | Mỗi `mcp__server__action` từ server |
| **MCP prompts/skills** | Slash commands, invoke qua `SkillTool` |
| **`MonitorMcpTask`** | Background task giám sát MCP process (feature flag) |
| **`components/mcp/`** | UI settings, tool list, auth flow (`/mcp`) |
| **Permissions** | Rules `mcp__server__*`, MCP instructions delta trong context |
| **Hooks** | `updatedMCPToolOutput` từ PostToolUse hooks |

---

## 14. Mental model

**Một MCP server ≠ một loại thing duy nhất.**

Một server process có thể expose đồng thời tools, resources, prompts. Claude Code tạo **một MCP SDK Client connection** per server, rồi map từng capability sang mechanism riêng:

- Tools → agent tool interface (model callable)
- Prompts → slash commands (user hoặc SkillTool)
- Resources → helper tools List/Read + cache

**Claude Code = MCP Client (nhiều connections).**

Mỗi connection độc lập, có state riêng, auth riêng, cache riêng. Agent chỉ thấy merged view từ tất cả connected servers.

**`services/mcp/` là lõi.**

Tool classes trong `tools/` là adapter mỏng để model tương tác với infrastructure đó.
