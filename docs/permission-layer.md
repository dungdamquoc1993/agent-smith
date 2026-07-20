# Permission Layer Notes

Tài liệu này ghi lại permission layer hiện tại và các điểm đã nhấn mạnh trong cuộc trao đổi review.

## Hình Dạng Hiện Tại

Permission layer hiện là guard runtime trước khi tool được execute. Nó nằm trong đường chạy của agent harness, không nằm trong LLM provider hay DB model.

Luồng chính:

1. Mỗi `AgentTool` khai báo `permission` spec và có thể có `check_permissions`.
2. `AgentHarness` gọi `resolve_harness_tool_permission` trước khi execute tool.
3. `PermissionResolver` trả về `allow`, `deny`, hoặc `ask`.
4. Nếu kết quả là `ask`, harness gọi `can_use_tool`.
5. Nếu không có `can_use_tool`, `ask` sẽ bị chuyển thành `deny`.

LiteLLM provider chỉ nhận tool schema để gửi cho model. Nó không enforce permission decision.

## Thứ Tự Quyết Định

Resolver hiện đánh giá permission theo thứ tự:

1. `hard_deny`
2. matching `deny` rule
3. `bypass` mode
4. `read_only` mode
5. matching `allow` rule
6. tool-specific `check_permissions`
7. `accept_edits` mode cho tool mutating files/resources
8. matching `ask` rule
9. default behavior của tool

Điểm quan trọng: một `deny` rule match tool sẽ mạnh hơn `bypass`, `accept_edits`, tool-level checks, allow rules, và tool defaults.

## Permission Modes

Các mode hiện có:

- `default`: đi theo rule, tool checks, và tool defaults.
- `read_only`: chỉ cho tool có `read_only=True`; deny các tool còn lại.
- `accept_edits`: auto-allow tool có `mutates_files=True`; các tool `ask` khác vẫn cần approval.
- `bypass`: allow hầu hết tool sau khi đã check `hard_deny` và `deny` rules.

Lưu ý: mode này trước đây được gọi là `plan`, nhưng tên đó khá giống Claude Code và gợi ý một plan-mode workflow mà Agent Smith chưa có trong harness. Tên canonical hiện là `read_only`, mô tả đúng behavior hơn: chỉ cho tool read-only chạy. Legacy value `plan` có thể được normalize về `read_only` để tránh làm gãy config cũ ngay lập tức.

`accept_edits` không đơn giản là "chỉ kém bypass một chút". Nó hẹp hơn nhiều: chỉ auto-allow tool được đánh dấu mutating files/resources. Các tool như task spawning hoặc non-read-only MCP vẫn rơi về `ask` nếu không được cover bởi allow rule hoặc custom tool check.

## Rules

Permission rules hiện match theo tool name bằng `fnmatch` pattern. Chúng chưa match theo args/action của tool.

Rule scopes:

- `session`
- `user`
- `project`
- `builtin`

Session-scoped rule bắt buộc có `session_id`. Child agent session có thể nhìn thấy rule từ parent session chain.

Rule store hiện là in-memory. Chưa có DB table riêng để persist permission rules.

## Deny Rules

`deny` rule là policy rule cấu hình được để chặn tool name match pattern. Nó khác với `hard_deny`:

- `hard_deny` được truyền trực tiếp vào `PermissionResolver`.
- `deny` rules đến từ rule provider/store.
- Cả hai đều được đánh giá trước các permissive modes.

Vì `deny` được check trước `bypass`, một deny rule match tool vẫn block tool ngay cả trong bypass mode.

## Hard Deny

Cơ chế `hard_deny` đã có, nhưng app service path hiện chưa cấu hình hard-deny case nào.

Mặc định `PermissionResolver` nhận `hard_deny=[]`. Hiện tại usage của `hard_deny` chỉ thấy trong tests, trừ khi caller tự inject resolver có hard-deny patterns.

## Tool Specs

Các predefined specs:

- `READ_ONLY_ALLOW`: default allow, `read_only=True`
- `MUTATING_ASK`: default ask, `mutates_files=True`
- `INTERACTIVE_ASK`: default ask, `requires_user_interaction=True`
- `TASK_ASK`: default ask
- `MCP_ASK`: default ask

Ví dụ:

- `web_fetch`, `web_search`, `skill`, `sleep`, `task_output`, và `todo` dùng read-only allow.
- `manage_resources` dùng mutating ask, nhưng `check_permissions` auto-allow `list` và `read`.
- `task` và `task_stop` dùng task ask.
- `ask_user_question` dùng interactive ask.

## MCP Tools

Permission cho MCP tool hiện tự động dựa trên metadata `read_only`:

- MCP tool `read_only=True` thành `READ_ONLY_ALLOW`.
- MCP tool còn lại thành `MCP_ASK`.

Chưa có custom permission config theo từng MCP server/tool. Nếu cần granular hơn, nên thêm policy kiểu allow/ask/deny theo MCP server name, tool name, hoặc generated agent tool name.

## `can_use_tool`

`can_use_tool` là host-side approval callback. Nó xử lý các decision có behavior `ask`.

Nó có thể:

- hỏi user approve/deny một tool call;
- route `ask_user_question` sang question handler;
- trả về updated tool input;
- persist allow/deny/ask rule cho các lần sau.

Nếu không truyền `can_use_tool`, harness không có chỗ nào để hỏi user. Khi đó một decision `ask` sẽ thành deny với source `missing_can_use_tool`.

App service hiện tạo `AgentRuntime` chỉ với `default_permission_mode`, chưa truyền `can_use_tool`. Điều này làm agent chạy qua path đó bị hạn chế trong `default` mode:

- read-only tools vẫn chạy;
- `manage_resources list/read` vẫn chạy nhờ tool-specific check;
- mutating resource actions, task spawning, non-read-only MCP tools, và user-question tools bị deny nếu rơi về `ask`.

## Các Hướng Tiếp Theo

Các việc đáng cân nhắc:

1. Wire `can_use_tool` vào app/UI path để `ask` thật sự là approval thay vì implicit deny.
2. Thêm persistent permission rule storage nếu session/user/project rules cần sống qua process restart.
3. Thêm MCP-specific permission config nếu read-only vs ask là quá thô.
4. Cân nhắc argument-aware rules cho tool như `manage_resources`, vì `list/read/create/update/delete` có risk profile khác nhau nhưng hiện chung một tool name.
