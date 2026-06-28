# Claude Code Permission Layer — Ghi chép kiến trúc

Tài liệu tổng hợp từ phân tích source leak `docs/knowledege_references/claude-code-leak/`. Mô tả cách permission layer hoạt động, mối quan hệ với `AskUserQuestion`, và hành vi khi sub-agent / swarm chạy song song.

---

## 1. Tổng quan: một pipeline cho mọi tool

Mọi tool (Bash, Edit, AskUserQuestion, …) đều đi qua **cùng một permission pipeline**. Không có kênh “hỏi user” riêng tách khỏi permission.

### Luồng thực thi tool

```
Model gọi tool_use
    → checkPermissionsAndCallTool (toolExecution.ts)
        → validate input
        → pre-tool hooks
        → resolveHookPermissionDecision → canUseTool(...)
            → hasPermissionsToUseTool()  // rule engine: allow | deny | ask
            → nếu ask: handleInteractivePermission → ToolUseConfirm queue
        → await Promise resolve
        → tool.call(updatedInput)
        → tool_result → resume query loop
```

**Điểm pause:** `await canUseTool(...)` trong `checkPermissionsAndCallTool`. Query loop đứng yên cho đến khi Promise resolve.

**Sau approve:** `permissionDecision.updatedInput` được merge vào input trước khi gọi `tool.call()`.

### Các module chính

| Module | Vai trò |
|--------|---------|
| `hasPermissionsToUseTool` (`utils/permissions/permissions.ts`) | Rule engine: deny / ask / allow; auto mode classifier; headless auto-deny |
| `useCanUseTool` (`hooks/useCanUseTool.tsx`) | Entry point: wrap Promise, route theo `behavior` |
| `handleInteractivePermission` (`hooks/toolPermission/handlers/interactiveHandler.ts`) | Push `ToolUseConfirm` vào queue, race bridge/channel/hooks/classifier |
| `handleCoordinatorPermission` | Worker coordinator: await hooks + classifier trước dialog |
| `handleSwarmWorkerPermission` | Swarm worker: forward qua mailbox tới leader |
| `PermissionRequest.tsx` | Router UI theo tool type |
| `PermissionContext.ts` | `handleUserAllow`, persist rules, logging, queue ops |

---

## 2. AskUserQuestion và permission layer — dùng chung gì?

`AskUserQuestion` **không phải** subsystem riêng. Nó là tool “permission-native”: hỏi user **bên trong** permission dialog.

### Cách tool cắm vào pipeline

```typescript
// AskUserQuestionTool.tsx
requiresUserInteraction() { return true; }

async checkPermissions(input) {
  return {
    behavior: 'ask',
    message: 'Answer questions?',
    updatedInput: input,
  };
}

async call({ questions, answers = {}, annotations }, _context) {
  // Pass-through — answers do permission UI thu thập
  return { data: { questions, answers, ... } };
}
```

Schema input có field `answers` với mô tả *"User answers collected by the permission component"*.

### UI routing

`PermissionRequest.tsx` map tool → component:

- `AskUserQuestionTool` → `AskUserQuestionPermissionRequest`
- `BashTool` → `BashPermissionRequest`
- …

Cùng contract: `PermissionRequestProps` với `toolUseConfirm`, `onDone`, `onReject`.

### Submit flow AskUserQuestion

1. Model gọi tool với `questions` (chưa có `answers`)
2. `checkPermissions` → `ask` → dialog multi-choice
3. User trả lời → `AskUserQuestionPermissionRequest` build `updatedInput` có `answers` + `annotations`
4. Gọi `toolUseConfirm.onAllow(updatedInput, ...)` — **cùng callback** với Bash allow
5. `tool.call()` nhận input đã có answers → trả `tool_result` cho model

### `requiresUserInteraction()` — ý nghĩa thực tế

| Hệ quả | Chi tiết |
|--------|----------|
| Không bypass auto mode | Step trong `permissions.ts`: vẫn `ask` dù mode auto |
| Không relay channel | Telegram/Discord yes-no không có `updatedInput` → skip |
| `call()` không thu input | Permission UI là nguồn dữ liệu |

### So sánh với tool “vô tình” cần permission (vd. Bash)

| | Bash / Edit / … | AskUserQuestion |
|--|-----------------|-----------------|
| Trigger `ask` | Rule, safety check, `checkPermissions` | Luôn `checkPermissions → ask` |
| UI | Tool-specific permission request | Multi-choice wizard |
| `updatedInput` | Command/path đã sửa | `{ questions, answers, annotations }` |
| `requiresUserInteraction` | Thường `false` | **`true`** |
| Classifier auto-approve | Có (Bash) | Không |

**Kết luận thiết kế:** Một `canUseTool` / await permission chung; mỗi tool chỉ cần `checkPermissions` + optional `PermissionRequest` component. AskUserQuestion là biến thể “interactive permission”, không phải chat channel riêng.

---

## 3. Interactive permission flow (main agent)

`useCanUseTool` khi `result.behavior === 'ask'`:

1. (Optional) `handleCoordinatorPermission` — await hooks + bash classifier
2. (Optional) `handleSwarmWorkerPermission` — forward mailbox nếu là swarm worker
3. (Optional) Speculative bash classifier race (2s grace)
4. `handleInteractivePermission` — push queue, setup callbacks

### ToolUseConfirm queue

Mỗi entry có:

- `onAllow(updatedInput, permissionUpdates, feedback, contentBlocks)`
- `onReject`, `onAbort`, `recheckPermission`, `onUserInteraction`
- Race với: CCR bridge, channel permission relay, PermissionRequest hooks, bash classifier

### UI: một dialog tại một thời điểm

REPL chỉ render **phần tử đầu queue**:

```tsx
// REPL.tsx
toolUseConfirmQueue[0]  // PermissionRequest overlay
onDone={() => setToolUseConfirmQueue(([_, ...tail]) => tail)}  // FIFO dequeue
```

`isWaitingForApproval = toolUseConfirmQueue.length > 0 || ...`

---

## 4. Sub-agent và permission — ba execution path

Claude Code có **ba cách** sub-agent/worker xử lý permission khác nhau.

### 4.1 Background sub-agent (`run_in_background: true`)

**Path phổ biến nhất** khi spawn 3–4 agent chạy nền.

#### Không hỏi user trên UI

`runAgent.ts` set `shouldAvoidPermissionPrompts: true` khi `isAsync` (trừ `bubble` mode hoặc explicit `canShowPermissionPrompts: true`):

```typescript
const shouldAvoidPrompts =
  canShowPermissionPrompts !== undefined
    ? !canShowPermissionPrompts
    : agentPermissionMode === 'bubble' ? false : isAsync;

if (shouldAvoidPrompts) {
  toolPermissionContext = { ...toolPermissionContext, shouldAvoidPermissionPrompts: true };
}
```

Khi tool cần `ask`:

```typescript
// permissions.ts — headless path
if (shouldAvoidPermissionPrompts) {
  const hookDecision = await runPermissionRequestHooksForHeadlessAgent(...);
  if (hookDecision) return hookDecision;
  return {
    behavior: 'deny',
    decisionReason: { type: 'asyncAgent', reason: 'Permission prompts are not available...' },
    message: AUTO_REJECT_MESSAGE(tool.name),
  };
}
```

Sub-agent nhận `tool_result` lỗi → model tự xử lý. **User không thấy dialog.**

#### Permission mode mặc định: `acceptEdits`

```typescript
// AgentTool.tsx
const workerPermissionContext = {
  ...appState.toolPermissionContext,
  mode: selectedAgent.permissionMode ?? 'acceptEdits',
};
```

Worker pool tools assemble với mode này → đa số Read/Write/Edit/Bash an toàn **auto-allow**, không tới bước `ask`.

#### Abort độc lập

Background agent có `AbortController` riêng — **không** link parent ESC. Kill qua `TaskStop` / `chat:killAgents`.

#### Tool set giới hạn

`ASYNC_AGENT_ALLOWED_TOOLS` — không có nested `AgentTool`, `TaskOutput`, swarm Task tools.

**Tại sao user không thấy 3–4 popup khi spawn parallel background agents:**

1. Headless by design — không có UI để hỏi
2. `acceptEdits` auto-allow hầu hết thao tác
3. Tool nhạy cảm → auto-deny im lặng, model/lead xử lý
4. Không tranh queue với main agent

---

### 4.2 Sync sub-agent (foreground)

- `isAsync: false` → **có thể** show permission dialog
- Dùng `canUseTool` của parent → **cùng `ToolUseConfirm` queue** với main agent
- Block parent turn cho đến khi agent xong (hoặc backgrounded)
- Thường chạy tuần tự trong một turn — ít gặp nhiều dialog song song

---

### 4.3 Swarm / in-process teammate (experimental, `--agent-teams`)

Nhiều worker song song **có thể** cần permission, nhưng vẫn **một dialog / một thời điểm**.

#### In-process teammate

`createInProcessCanUseTool` (`inProcessRunner.ts`):

- Gọi `hasPermissionsToUseTool`
- Nếu `ask` → push vào **leader's queue** qua `registerLeaderToolUseConfirmQueue`
- Entry có `workerBadge: { name, color }` — user biết request từ teammate nào
- Worker **block** trên Promise cho đến leader approve

#### Tmux / separate process worker

`handleSwarmWorkerPermission`:

1. Thử bash classifier auto-approve
2. Gửi `permission_request` qua teammate mailbox
3. Set `pendingWorkerRequest` (indicator trên worker pane)
4. Leader `useInboxPoller` nhận → append vào **cùng queue**, dedupe theo `toolUseID`
5. User approve → `sendPermissionResponseViaMailbox` → worker resume

#### Queue FIFO + dedupe

```typescript
// useInboxPoller.ts — leader side
setToolUseConfirmQueue(queue => {
  if (queue.some(q => q.toolUseID === parsed.tool_use_id)) return queue;
  return [...queue, entry];
});
```

User approve → `onDone` shift tail → dialog tiếp theo. **Không bao giờ 4 modal chồng nhau.**

#### `awaitAutomatedChecksBeforeDialog`

Background agent **có thể** show prompt (in-process teammate): await hooks + classifier **trước** khi interrupt user — chỉ hỏi khi automation không resolve được.

---

## 5. Bảng tóm tắt theo scenario

| Scenario | Hỏi user? | N agent parallel |
|----------|-----------|------------------|
| **Background sub-agent** | Không — auto-deny + acceptEdits | Headless, im lặng |
| **Sync sub-agent** | Có thể — queue của lead | Thường tuần tự |
| **Swarm teammate** | Có — queue leader + badge | FIFO, 1 dialog, còn lại chờ |

---

## 6. Các cơ chế phụ (race / remote)

| Cơ chế | Mô tả |
|--------|--------|
| **CCR Bridge** | Forward permission tới claude.ai; `claim()` race với local UI |
| **Channel relay** | Telegram/Discord yes-no; skip `requiresUserInteraction` tools |
| **Bash classifier** | Auto-approve high-confidence; race với user interaction (main agent only) |
| **PermissionRequest hooks** | Hook có thể allow/deny trước dialog |
| **PreToolUse hooks** | `resolveHookPermissionDecision` — hook allow vẫn phải qua rule check |

---

## 7. Gợi ý áp dụng cho agent-smith

Pattern đáng replicate:

1. **Một permission pipeline** cho mọi tool — `checkPermissions` → `canUseTool` Promise → `tool.call(updatedInput)`.
2. **Interactive tools** (`AskUserQuestion`-like): `requiresUserInteraction()`, UI thu data → inject vào `updatedInput` qua `onAllow`, `call()` pass-through.
3. **Background workers**: `should_avoid_permission_prompts` + relaxed mode (`accept_edits`) → tránh popup treo + giảm deny noise.
4. **Mọi interactive prompt** (main + N workers) funnel vào **một FIFO queue** trên leader session.
5. **Worker identity** trên dialog (badge) khi request từ sub-agent/swarm.

---

## 8. File tham chiếu chính (trong leak)

```
src/hooks/useCanUseTool.tsx
src/hooks/toolPermission/PermissionContext.ts
src/hooks/toolPermission/handlers/interactiveHandler.ts
src/hooks/toolPermission/handlers/coordinatorHandler.ts
src/hooks/toolPermission/handlers/swarmWorkerHandler.ts
src/hooks/useInboxPoller.ts
src/utils/permissions/permissions.ts
src/services/tools/toolExecution.ts
src/services/tools/toolHooks.ts          # resolveHookPermissionDecision
src/components/permissions/PermissionRequest.tsx
src/components/permissions/AskUserQuestionPermissionRequest/
src/tools/AskUserQuestionTool/AskUserQuestionTool.tsx
src/tools/AgentTool/runAgent.ts
src/tools/AgentTool/AgentTool.tsx
src/utils/swarm/inProcessRunner.ts       # createInProcessCanUseTool
src/utils/swarm/permissionSync.ts
src/utils/swarm/leaderPermissionBridge.ts
src/screens/REPL.tsx                     # toolUseConfirmQueue, overlay
docs/multi-agent.md
```

---

*Tạo: 2026-06-28 — tổng hợp từ session phân tích permission layer + sub-agent behavior.*
