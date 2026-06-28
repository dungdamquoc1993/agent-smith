# Kiến trúc Multi-Agent trong Claude Code

Claude Code có **hai hệ multi-agent độc lập** với thiết kế, mục đích và availability hoàn toàn khác nhau.

---

## Tổng quan — Hai hệ

| | Hệ 1: Subagent (Background Agent) | Hệ 2: Swarm (Agent Teams) |
|---|---|---|
| **Trạng thái** | Available cho tất cả users | Experimental, chưa public |
| **Kích hoạt** | AgentTool với `run_in_background: true` | `--agent-teams` flag + GrowthBook gate |
| **Mô hình** | Hierarchical (lead điều khiển worker) | Shared state (mọi agent thấy task chung) |
| **Giao tiếp** | Lead gửi prompt → nhận output qua TaskOutput | Shared filesystem (`~/.claude/tasks/<team>/`) |
| **UX** | Worker chạy ngầm, user không thấy | Terminal split panes, tất cả visible |
| **Tools** | AgentTool, TaskOutput, TaskStop | TaskCreate/Get/List/Update, SendMessage |

---

## Hệ 1 — Subagent (Background Agent)

Đây là hệ bạn dùng khi chạy `claude` bình thường hiện nay.

### Ba tools quản lý vòng đời

```
AgentTool   → spawn + run một agent (tạo ra "job")
TaskOutput  → poll output của job đó (đọc từ disk)
TaskStop    → kill job đang chạy
```

### Cách hoạt động

Khi AgentTool được gọi với `run_in_background: true`:

1. Spawn worker trong cùng process (async)
2. Trả về ngay `{ status: 'async_launched', task_id: '...', outputFile: '...' }`
3. Worker chạy nền, ghi output ra disk
4. Lead dùng `TaskOutput(task_id, block: true)` để đọc kết quả khi cần

Khi gọi **không có** `run_in_background` (hoặc `false`): chạy sync, block cho đến khi xong rồi mới trả về.

### Tool set của async agent

Worker bị giới hạn bộ `ASYNC_AGENT_ALLOWED_TOOLS`:
```
Read, Write, Edit, Bash, Glob, Grep,
WebSearch, WebFetch, NotebookEdit,
TodoWrite, Skill, ToolSearch,
EnterWorktree, ExitWorktree, StructuredOutput
```

**Bị chặn có chủ đích:**
- `AgentTool` — tránh đệ quy không kiểm soát
- `TaskOutput` — tránh đệ quy poll
- `TaskStop` — chỉ main thread mới có quyền kill tasks
- Bộ 4 Task tools (TaskCreate/Get/List/Update) — dành riêng cho swarm

### Các execution path của AgentTool

AgentTool có 4 trạng thái trả về tùy context:

| `status` | Nghĩa | Điều kiện |
|---|---|---|
| `completed` | Sync, đã xong, kết quả đính kèm | Mặc định |
| `async_launched` | Background, trả về task_id | `run_in_background: true` |
| `teammate_spawned` | Spawn tmux/in-process teammate | Swarm enabled |
| `remote_launched` | Chạy trên CCR remote | `USER_TYPE === 'ant'` only |

### Tên legacy

AgentTool trước đây có wire name là `Task` (xem `LEGACY_AGENT_TOOL_NAME`). Đó là lý do một số hooks/permission rules cũ vẫn dùng tên `Task`.

---

## Hệ 2 — Agent Swarm (Agent Teams)

**Trạng thái hiện tại:** Experimental, bị chặn bởi GrowthBook gate `tengu_amber_flint`. External users chưa được access kể cả khi pass flag.

### Thiết kế intent

Người dùng chỉ nói chuyện với một **lead agent** duy nhất. Lead tự quyết định khi nào cần spawn teammates, phân công việc qua shared task list, teammates làm song song, lead tổng hợp và trả kết quả về cho user. User thấy terminal được split thêm panes cho mỗi teammate.

```
User ←→ Lead Agent
           ├── [spawn] Teammate A  ─→ làm Task 1, 2
           ├── [spawn] Teammate B  ─→ làm Task 3
           └── [spawn] Teammate C  ─→ làm Task 4, 5
                    ↕ ↕ ↕
           ~/.claude/tasks/<team-name>/*.json
           (shared task list — tất cả đọc/ghi chung)
```

### Bộ 4 tools điều phối nội bộ team

Đây là **intra-team coordination tools** — chỉ available cho swarm teammates, không available cho async agents:

| Tool | Chức năng |
|---|---|
| `TaskCreate` | Tạo task mới trong shared task list |
| `TaskGet` | Đọc thông tin một task |
| `TaskList` | Liệt kê tất cả tasks trong team |
| `TaskUpdate` | Cập nhật status, owner, dependencies |

Task list lưu trên disk tại `~/.claude/tasks/<teamName>/` — tất cả members của cùng team đọc/ghi vào cùng thư mục này, kể cả tmux processes riêng lẻ.

**Khác với `TodoWrite`:**
- `TodoWrite` — session-scoped, chỉ 1 agent tự dùng để tracking
- Bộ 4 Task tools — team-scoped, shared state cho cả team, agent nào cũng thấy ai đang làm gì

**Cross-team communication** dùng `SendMessage` — message passing trực tiếp giữa các agents, không qua task list.

### Hai backend spawn teammate

**1. tmux backend** — spawn process mới trong tmux pane:
```bash
# Code gọi nội bộ (không phải bạn gõ):
tmux split-pane
claude --agent-id researcher@alpha --team-name alpha --parent-session-id <id>
```
Mỗi teammate là OS process độc lập. Cần tmux (hoặc iTerm2 trên Mac).

**2. In-process backend** — chạy trong cùng Node.js process, tách context bằng `AsyncLocalStorage`. Nhẹ hơn, không cần tmux. Được ưu tiên khi available.

### Flow spawn đầy đủ

```
1. AgentTool(name="researcher", prompt="...", team_name="alpha")
        ↓
2. TeamCreate tool tạo team file + tasks directory (nếu chưa có)
        ↓
3. Detect backend: in-process hoặc tmux/iTerm2
        ↓
4. Spawn teammate với identity args:
   --agent-id researcher@alpha
   --agent-name researcher
   --team-name alpha
   --parent-session-id <lead-session-id>
        ↓
5. Initial prompt ghi vào "mailbox" (file disk)
        ↓
6. Teammate process khởi động, đọc mailbox, bắt đầu làm việc
        ↓
7. Teammates xong → tự terminate hoặc lead gọi TaskStop
```

### Kích hoạt (cho external users khi được mở)

```bash
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 claude --agent-teams
```

Cần **cả hai điều kiện**:
1. Opt-in qua env var hoặc `--agent-teams`
2. GrowthBook gate `tengu_amber_flint` = true (server-side, Anthropic kiểm soát)

Anthropic internal (`USER_TYPE=ant`): luôn bật, không cần flag.

### Lưu ý: Mở nhiều terminal thủ công ≠ Swarm

Nếu bạn mở 2 cửa sổ terminal và chạy `claude` riêng lẻ, chúng không tạo thành swarm vì:
- Không có `--team-name` → mỗi process dùng session ID riêng làm `taskListId`
- Không có shared task directory
- Không có `SendMessage` routing giữa chúng

---

## So sánh thiết kế

| Khía cạnh | Subagent | Swarm |
|---|---|---|
| **Quyền lực** | Lead là nguồn sự thật duy nhất | Lead vẫn là orchestrator nhưng teammates có shared visibility |
| **Worker biết gì** | Chỉ biết prompt được giao | Biết toàn bộ task list, ai làm gì, task nào blocked |
| **Pick up công việc** | Lead phải giao lại | Teammate có thể pick up task của teammate khác tự động |
| **Transparency với user** | Ngầm, user thấy lead "chờ" | Explicit, user thấy tất cả panes làm việc song song |

---

## Source files liên quan

```
src/tools/AgentTool/           — AgentTool chính, constants, UI
src/tools/TaskOutputTool/      — Poll output async agent
src/tools/TaskStopTool/        — Kill task/agent
src/tools/TaskCreateTool/      — Tạo task trong shared list
src/tools/TaskGetTool/         — Đọc task
src/tools/TaskListTool/        — Liệt kê tasks
src/tools/TaskUpdateTool/      — Cập nhật task
src/tools/shared/spawnMultiAgent.ts  — Logic spawn tmux/in-process teammate
src/tasks/InProcessTeammateTask/     — In-process teammate lifecycle
src/utils/agentSwarmsEnabled.ts      — Gate check cho swarm feature
src/utils/swarm/               — Toàn bộ swarm utilities (teamHelpers, backends, layouts...)
src/constants/tools.ts         — ASYNC_AGENT_ALLOWED_TOOLS, IN_PROCESS_TEAMMATE_ALLOWED_TOOLS
```
